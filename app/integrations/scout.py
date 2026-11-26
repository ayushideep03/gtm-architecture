"""
Scout (kiryano/Scout, MIT) profile adapter — deliberately narrow.

Scout scrapes ONE known profile per call (no search). Per the integration
assessment this adapter:
  - runs Scout's scraper functions in a subprocess from a local checkout
    (``settings.scout_path``; its package name ``app`` would clash with ours),
  - only allows platforms in ``settings.platforms`` (default: github, youtube;
    LinkedIn cookie scraping and free proxies are not used),
  - never runs Scout's SMTP probing / email guessing,
  - emits records whose source starts with "scout", which the ledger treats as
    weak evidence (config memory.source_evidence_kinds) — guessed contact data
    is proposed for review, never used for outreach.

Input: ``criteria.extra = {"profiles": [{"platform": "github", "username": "..."}]}``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

from app.integrations.lead_source import LeadSourceProvider, ProviderError
from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord

_RUNNER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import app.scrapers as scrapers
out = []
for item in json.loads(sys.argv[2]):
    fn = getattr(scrapers, "scrape_" + item["platform"], None)
    try:
        out.append({"request": item, "profile": fn(item["username"]) if fn else None,
                    "error": None if fn else "unsupported platform"})
    except Exception as exc:
        out.append({"request": item, "profile": None, "error": str(exc)})
print(json.dumps(out, default=str))
"""


class ScoutProfileProvider(LeadSourceProvider):
    def __init__(self, scout_path: str, name: str = "scout", python: Optional[str] = None,
                 platforms: Optional[list[str]] = None, timeout_s: float = 60.0, **_: Any) -> None:
        self._path = str(Path(scout_path).expanduser())
        self._name = name
        self._python = python or sys.executable
        self.platforms = platforms or ["github", "youtube"]
        self._timeout = timeout_s

    @classmethod
    def from_config(cls, pc, credentials: dict) -> "ScoutProfileProvider":
        from app.providers.loader import ProviderUnavailable
        path = pc.settings.get("scout_path")
        if not path or not (Path(path).expanduser() / "app" / "scrapers").is_dir():
            raise ProviderUnavailable("settings.scout_path must point to a local Scout checkout")
        return cls(name=pc.name, timeout_s=pc.resilience.timeout_s, **pc.settings)

    @property
    def provider_name(self) -> str:
        return self._name

    async def search_leads(self, criteria: LeadSearchCriteria) -> list[LeadSourceRecord]:
        requests = [p for p in (criteria.extra or {}).get("profiles", []) if p.get("platform") in self.platforms]
        if not requests:
            return []
        proc = await asyncio.create_subprocess_exec(
            self._python, "-I", "-c", _RUNNER, self._path, json.dumps(requests),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=self._path)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ProviderError(self._name, "Scout timed out") from exc
        if proc.returncode != 0:
            raise ProviderError(self._name, f"Scout failed: {err.decode()[-300:]}")
        return [r for r in (self._to_record(row) for row in json.loads(out.decode() or "[]")) if r]

    def _to_record(self, row: dict) -> Optional[LeadSourceRecord]:
        p, req = row.get("profile"), row["request"]
        if not p:
            return None
        platform = req["platform"]
        linkedin = p.get("profile_url") if platform == "linkedin" else (p.get("socials") or {}).get("linkedin")
        return LeadSourceRecord(
            source=f"scout:{platform}",
            external_id=f"{platform}:{p.get('channel_id') or p.get('username') or req['username']}",
            full_name=p.get("full_name"),
            email=p.get("email") or p.get("business_email"),      # weak evidence: proposed only
            job_title=p.get("headline"),
            linkedin_url=linkedin,
            company_name=p.get("company"),
            company_website=p.get("website"),
            location=p.get("location"),
            raw_data={k: v for k, v in p.items() if k not in ("possible_emails",)},
        )
