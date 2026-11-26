"""
Apollo.io lead source.

Built from Apollo's API reference (People API Search + People Enrichment);
NOT yet verified against a live Apollo account.

- Search: POST https://api.apollo.io/api/v1/mixed_people/api_search (0 credits).
  Returns people with an obfuscated last name, no email, no company domain.
- Enrich: POST https://api.apollo.io/api/v1/people/match per person (consumes
  credits) to get name, email, LinkedIn, company website. Capped per search by
  ``max_enrich_per_search``; set ``enrich: false`` to skip (records then carry
  only what search returns — the obfuscated last name is never stored).
- Auth: ``x-api-key`` header from the env var named in config credentials.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.integrations.lead_source import LeadSourceProvider, ProviderError
from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord

BASE_URL = "https://api.apollo.io/api/v1"


def _domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    return host[4:] if host.startswith("www.") else host or None


class ApolloProvider(LeadSourceProvider):
    def __init__(self, api_key: str, name: str = "apollo", enrich: bool = True, max_enrich_per_search: int = 10,
                 base_url: str = BASE_URL, timeout_s: float = 30.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None, **_: Any) -> None:
        self._key = api_key
        self._name = name
        self.enrich = enrich
        self.max_enrich = max_enrich_per_search
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    @classmethod
    def from_config(cls, pc, credentials: dict) -> "ApolloProvider":
        return cls(name=pc.name, timeout_s=pc.resilience.timeout_s, **{**pc.settings, **credentials})

    @property
    def provider_name(self) -> str:
        return self._name

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, timeout=self._timeout, transport=self._transport,
                                 headers={"x-api-key": self._key, "Content-Type": "application/json",
                                          "Cache-Control": "no-cache"})

    @staticmethod
    def search_params(c: LeadSearchCriteria) -> list[tuple[str, Any]]:
        params: list[tuple[str, Any]] = [("page", 1), ("per_page", max(1, min(c.limit, 100)))]
        for t in c.job_titles or []:
            params.append(("person_titles[]", t))
        if c.company_domain:
            params.append(("q_organization_domains_list[]", c.company_domain))
        if c.location:
            params.append(("person_locations[]", c.location))
        if c.keywords:
            params.append(("q_keywords", " ".join(c.keywords)))
        extra = c.extra or {}
        for s in extra.get("seniorities", []):
            params.append(("person_seniorities[]", s))
        for r in extra.get("employee_ranges", []):          # e.g. "50,1000"
            params.append(("organization_num_employees_ranges[]", r))
        for i in extra.get("industries", []):
            params.append(("organization_industries[]", i))
        return params

    async def _post(self, client: httpx.AsyncClient, path: str, params) -> dict:
        try:
            resp = await client.post(path, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(self._name, f"network error: {exc}") from exc
        if resp.status_code == 429:
            raise ProviderError(self._name, "rate limited by Apollo (429)")
        if resp.status_code in (401, 403):
            raise ProviderError(self._name, f"Apollo rejected the API key or scope ({resp.status_code})")
        if resp.status_code >= 400:
            raise ProviderError(self._name, f"Apollo error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def search_leads(self, criteria: LeadSearchCriteria) -> list[LeadSourceRecord]:
        async with self._client() as client:
            data = await self._post(client, "/mixed_people/api_search", self.search_params(criteria))
            people = data.get("people") or []
            records: list[LeadSourceRecord] = []
            for i, p in enumerate(people):
                enriched = None
                if self.enrich and i < self.max_enrich and p.get("id"):
                    enriched = (await self._post(client, "/people/match", [("id", p["id"])])).get("person")
                records.append(self._to_record(p, enriched))
            return records

    def _to_record(self, searched: dict, enriched: Optional[dict]) -> LeadSourceRecord:
        src = enriched or {}
        org = (src.get("organization") or searched.get("organization") or {})
        return LeadSourceRecord(
            source=self._name,
            external_id=str(searched.get("id")),
            first_name=src.get("first_name") or searched.get("first_name"),
            last_name=src.get("last_name"),                 # never the obfuscated search value
            full_name=src.get("name"),
            email=src.get("email") if src.get("email_status") in ("verified", "likely to engage", None) else None,
            job_title=src.get("title") or searched.get("title"),
            linkedin_url=src.get("linkedin_url"),
            company_name=org.get("name"),
            company_domain=org.get("primary_domain") or _domain(org.get("website_url")),
            company_website=org.get("website_url"),
            company_linkedin_url=org.get("linkedin_url"),
            industry=org.get("industry"),
            employee_count=org.get("estimated_num_employees"),
            location=", ".join(filter(None, [src.get("city"), src.get("state"), src.get("country")])) or None,
            raw_data={"search": searched, "enriched": bool(enriched),
                      "email_status": src.get("email_status"), "match_confidence": src.get("match_confidence")},
        )
