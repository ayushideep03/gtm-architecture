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

import re
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


class WebsiteFetcher:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._transport = transport   # injectable for tests

    async def fetch_site(self, domain: str, policy: ResearchPolicy) -> list[Page]:
        base = f"https://{domain}"
        pages: list[Page] = []
        headers = {"User-Agent": policy.user_agent, "Accept": "text/html"}
        async with httpx.AsyncClient(timeout=policy.timeout_s, follow_redirects=True, headers=headers,
                                     transport=self._transport) as client:
            robots: Optional[RobotFileParser] = None
            if policy.respect_robots_txt:
                robots = RobotFileParser()
                try:
                    r = await client.get(urljoin(base, "/robots.txt"))
                    robots.parse(r.text.splitlines() if r.status_code == 200 else [])
                except httpx.HTTPError:
                    robots.parse([])
            for path in policy.paths:
                if len(pages) >= policy.max_pages:
                    break
                url = urljoin(base, path)
                if robots is not None and not robots.can_fetch(policy.user_agent, url):
                    logger.info("robots.txt disallows %s", url)
                    continue
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as exc:
                    logger.info("Research fetch failed for %s: %s", url, exc)
                    continue
                final = str(resp.url)
                if resp.status_code != 200 or not same_site(final, domain):
                    continue
                if "html" not in resp.headers.get("content-type", ""):
                    continue
                title, text = html_to_text(resp.text[: policy.max_chars_per_page * 4])
                if text and not any(p.url == final for p in pages):
                    pages.append(Page(url=final, title=title, text=text[: policy.max_chars_per_page]))
        return pages
