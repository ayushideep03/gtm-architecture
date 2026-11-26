"""
Minimal, conservative website reader for prospect research.

- Only the prospect company's own domain (and its subdomains) — never a URL
  suggested by the model or found on a page.
- robots.txt respected (configurable), generic user agent, timeouts, size caps.
- HTML is reduced to visible text; scripts/styles dropped.

The fetched text is UNTRUSTED data: it is passed to the LLM only as quoted
source material, and every research claim must quote it verbatim (checked
deterministically by the research provider).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional, Protocol
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.gtm_config import ResearchPolicy
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Page:
    url: str
    title: str
    text: str


class Fetcher(Protocol):
    async def fetch_site(self, domain: str, policy: ResearchPolicy) -> list[Page]: ...


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "template", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML still yields partial text
        pass
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return parser.title.strip(), text


def same_site(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
MAX_REDIRECTS = 3


class UnsafeTarget(ValueError):
    """The research target is not a public website (SSRF guard)."""


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or getattr(ip, "is_site_local", False))


async def resolve_public(host: str) -> None:
    """Raise UnsafeTarget unless ``host`` is a DNS name whose every address is public."""
    host = (host or "").lower().rstrip(".")
    if not _HOSTNAME.match(host) or host.endswith((".local", ".internal", ".localhost", ".lan", ".home")):
        raise UnsafeTarget(f"not a public hostname: {host!r}")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeTarget(f"cannot resolve {host!r}: {exc}") from exc
    for info in infos:
        if not is_public_ip(info[4][0]):
            raise UnsafeTarget(f"{host!r} resolves to a non-public address")


class WebsiteFetcher:
    """Reads a prospect's public website. SSRF-guarded: only public hostnames that resolve to
    public addresses, redirects followed manually (each hop re-checked, same site only), and
    responses streamed with a byte cap. DNS rebinding between check and connect remains a
    residual risk; run research egress through a proxy that blocks private ranges in production."""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None, check_dns: Optional[bool] = None) -> None:
        self._transport = transport   # injectable for tests
        # Mock transports never touch the network, so DNS checks default off for them.
        self._check_dns = (transport is None) if check_dns is None else check_dns
        self._checked: set[str] = set()

    async def _guard(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host in self._checked:
            return
        if self._check_dns:
            await resolve_public(host)
        elif not _HOSTNAME.match(host):
            raise UnsafeTarget(f"not a public hostname: {host!r}")
        self._checked.add(host)

    async def _get(self, client: httpx.AsyncClient, url: str, domain: str, max_bytes: int) -> tuple[str, int, str, str]:
        """GET with manual, re-validated redirects and a byte cap. Returns (final_url, status, ctype, text)."""
        for _ in range(MAX_REDIRECTS + 1):
            await self._guard(url)
            async with client.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    nxt = urljoin(url, resp.headers["location"])
                    if urlparse(nxt).scheme not in ("http", "https") or not same_site(nxt, domain):
                        return nxt, 0, "", ""       # off-site redirect: never followed
                    url = nxt
                    continue
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                body = b"".join(chunks)[:max_bytes]
                return str(resp.url), resp.status_code, resp.headers.get("content-type", ""), \
                    body.decode(resp.encoding or "utf-8", "replace")
        return url, 0, "", ""

    async def fetch_site(self, domain: str, policy: ResearchPolicy) -> list[Page]:
        domain = (domain or "").lower().strip().rstrip(".")
        base = f"https://{domain}"
        pages: list[Page] = []
        try:
            await self._guard(base)
        except UnsafeTarget as exc:
            logger.warning("Research refused: %s", exc)
            return pages
        headers = {"User-Agent": policy.user_agent, "Accept": "text/html"}
        max_bytes = policy.max_chars_per_page * 4
        async with httpx.AsyncClient(timeout=policy.timeout_s, follow_redirects=False, headers=headers,
                                     transport=self._transport) as client:
            robots: Optional[RobotFileParser] = None
            if policy.respect_robots_txt:
                robots = RobotFileParser()
                try:
                    _, code, _, text = await self._get(client, urljoin(base, "/robots.txt"), domain, 256_000)
                    robots.parse(text.splitlines() if code == 200 else [])
                except (httpx.HTTPError, UnsafeTarget):
                    robots.parse([])
            for path in policy.paths:
                if len(pages) >= policy.max_pages:
                    break
                url = urljoin(base, path)
                if robots is not None and not robots.can_fetch(policy.user_agent, url):
                    logger.info("robots.txt disallows %s", url)
                    continue
                try:
                    final, code, ctype, raw = await self._get(client, url, domain, max_bytes)
                except (httpx.HTTPError, UnsafeTarget) as exc:
                    logger.info("Research fetch failed for %s: %s", url, exc)
                    continue
                if code != 200 or not same_site(final, domain) or "html" not in ctype:
                    continue
                title, text = html_to_text(raw)
                if text and not any(p.url == final for p in pages):
                    pages.append(Page(url=final, title=title, text=text[: policy.max_chars_per_page]))
        return pages
