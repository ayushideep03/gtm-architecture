"""CSV lead import: header aliases from config -> LeadSourceRecord -> normal ingestion pipeline."""

from __future__ import annotations

import csv
import io
from typing import Optional

from app.core.gtm_config import get_gtm_config
from app.integrations.lead_source_record import LeadSourceRecord


class ImportError_(ValueError):
    pass


def parse_csv(text: str, source: str) -> tuple[list[LeadSourceRecord], list[str]]:
    cfg = get_gtm_config().lead_import
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise ImportError_("CSV has no header row")
    header = {h: (h or "").strip().lower() for h in reader.fieldnames}
    mapping: dict[str, str] = {}
    for field, aliases in cfg.column_aliases.items():
        for raw, norm in header.items():
            if norm in aliases and field not in mapping:
                mapping[field] = raw
    if not ({"email", "full_name", "first_name", "linkedin_url"} & set(mapping)):
        raise ImportError_(f"No identifying column found; headers were {list(header.values())}")

    records, warnings = [], []
    for n, row in enumerate(reader, start=2):
        if n - 1 > cfg.max_rows:
            warnings.append(f"stopped after {cfg.max_rows} rows")
            break
        values = {f: (row.get(col) or "").strip() or None for f, col in mapping.items()}
        employees: Optional[int] = None
        if values.get("employee_count"):
            digits = "".join(ch for ch in values["employee_count"] if ch.isdigit())
            employees = int(digits) if digits else None
        domain = values.get("company_domain")
        records.append(LeadSourceRecord(
            source=source, external_id=values.get("external_id") or (f"row-{n}" if not values.get("email") else None),
            first_name=values.get("first_name"), last_name=values.get("last_name"), full_name=values.get("full_name"),
            email=values.get("email"), phone=values.get("phone"), job_title=values.get("job_title"),
            linkedin_url=values.get("linkedin_url"), company_name=values.get("company_name"),
            company_domain=domain, company_website=domain if domain and "." in domain else None,
            industry=values.get("industry"), employee_count=employees, location=values.get("location"),
            raw_data={"row": n},
        ))
    return records, warnings
