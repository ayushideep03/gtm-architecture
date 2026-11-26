"""
Evidence ledger and fact lifecycle.

Every important value about a company, person or lead is recorded as a Fact
with one or more pieces of evidence (kind, source, timestamp). The score is
computed from configured evidence weights, never supplied by an AI:

    score = 1 - prod(1 - w_i)    (noisy-OR, capped at 0.99)
    any 'contradiction' evidence caps the score
    band  = verified (>= verified_threshold AND a primary source)
          | probable (>= probable_threshold) | possible

Lifecycle (deterministic):
    applied     the value is written to the CRM record. Happens when the field
                is empty and the fact is at least 'probable', when the fact meets
                overwrite_min_band, or when a human entered it.
    proposed    kept for review: a weak claim, a conflict with an existing
                value, or a value a human entered earlier (never overwritten).
    superseded  an applied fact replaced by a newer applied fact.
    dismissed   rejected by a reviewer; the same value is never re-proposed.

Identity keys (email, domain) are additionally protected: applying them must
not collide with another record of the same tenant.

The CRM columns remain the fast read model; the ledger is the provenance.
(Scoring and lifecycle pattern adapted from trycompai/crm, MIT licence.)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gtm_config import MemoryConfig, get_gtm_config
from app.core.logging import get_logger
from app.models.base import Company, Lead, Person
from app.models.memory import Fact, FactEvidence

logger = get_logger(__name__)

ENTITY_MODELS: dict[str, type] = {"company": Company, "person": Person, "lead": Lead}
_MODEL_TYPES = {v: k for k, v in ENTITY_MODELS.items()}
# Never written through the ledger (identity / bookkeeping columns).
_PROTECTED = {"id", "tenant_id", "created_at", "updated_at", "is_open", "company_id", "person_id",
              "enriched_at", "enrichment_source", "status", "score"}
_IDENTITY_KEYS = {"company": {"domain"}, "person": {"email"}}
_BAND_ORDER = {"possible": 0, "probable": 1, "verified": 2}


@dataclass
class EvidenceInput:
    kind: str
    detail: Optional[str] = None
    source_url: Optional[str] = None
    source_ref: Optional[str] = None
    observed_at: Optional[datetime] = None


@dataclass
class FactOutcome:
    fact: Optional[Fact]
    status: str                 # applied | proposed | dismissed (value previously rejected)
    changed: bool = False       # CRM column value changed
    reason: str = ""


@dataclass
class Score:
    score: float
    band: str
    primary: bool
    breakdown: list[dict] = field(default_factory=list)


def score_evidence(evidence: Iterable[EvidenceInput], cfg: MemoryConfig) -> Score:
    remaining, primary, contradicted, parts = 1.0, False, False, []
    for ev in evidence:
        kind = cfg.evidence_kinds.get(ev.kind)
        if kind is None:
            raise ValueError(f"unknown evidence kind {ev.kind!r}; configure it under memory.evidence_kinds")
        if ev.kind == "contradiction":
            contradicted = True
        remaining *= (1.0 - kind.weight)
        primary = primary or kind.primary
        parts.append({"kind": ev.kind, "weight": kind.weight, "primary": kind.primary})
    score = min(1.0 - remaining, 0.99)
    if contradicted:
        score = min(score, cfg.contradiction_cap)
    if score >= cfg.verified_threshold and primary:
        band = "verified"
    elif score >= cfg.probable_threshold:
        band = "probable"
    else:
        band = "possible"
    return Score(round(score, 4), band, primary, parts)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    return value


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return json.dumps(_jsonable(a), sort_keys=True, default=str) == json.dumps(_jsonable(b), sort_keys=True, default=str)


def _column_value(model: type, field_name: str, value: Any) -> Any:
    col = model.__table__.c[field_name]
    if isinstance(col.type, Integer):
        return int(value) if value is not None else None
    if isinstance(col.type, (String, Text)) and value is not None and not isinstance(value, str):
        return json.dumps(value, default=str)
    return value


def _current(entity: Any, field_name: str) -> Any:
    if not hasattr(entity, field_name):
        return None
    value = getattr(entity, field_name)
    col = type(entity).__table__.c.get(field_name)
    if value is not None and col is not None and isinstance(col.type, Text) and isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return parsed
        except ValueError:
            pass
    return value


class FactLedger:
    def __init__(self, config: Optional[MemoryConfig] = None) -> None:
        self._config = config

    def _cfg(self, tenant_id) -> MemoryConfig:
        return self._config or get_gtm_config().for_tenant(tenant_id).memory

    @staticmethod
    def writable(model: type, field_name: str) -> bool:
        return field_name in model.__table__.c and field_name not in _PROTECTED

    async def record(
        self,
        db: AsyncSession,
        entity: Any,
        field_name: str,
        value: Any,
        evidence: list[EvidenceInput],
        *,
        method: Optional[str] = None,
        rationale: Optional[str] = None,
        source_task_id: Optional[uuid.UUID] = None,
        decided_by: Optional[str] = None,
    ) -> FactOutcome:
        if value in (None, "", [], {}):
            return FactOutcome(None, "skipped", reason="empty value")
        entity_type = _MODEL_TYPES[type(entity)]
        model = type(entity)
        cfg = self._cfg(entity.tenant_id)
        score = score_evidence(evidence, cfg)
        human = any(ev.kind == "human_entered" for ev in evidence)
        value = _jsonable(value)

        facts = (await db.execute(select(Fact).where(
            Fact.tenant_id == entity.tenant_id, Fact.entity_type == entity_type,
            Fact.entity_id == entity.id, Fact.field == field_name,
            Fact.status.in_(("applied", "proposed", "dismissed"))))).scalars().all()
        applied = next((f for f in facts if f.status == "applied"), None)

        # Previously rejected value: don't re-propose (a human can still enter it).
        if not human and any(f.status == "dismissed" and _same(f.value, value) for f in facts):
            return FactOutcome(None, "dismissed", reason="value was previously dismissed by a reviewer")

        # Corroboration: same value as the applied fact -> add evidence, re-score.
        if applied is not None and _same(applied.value, value):
            await self._add_evidence(db, applied, evidence)
            all_ev = [EvidenceInput(kind=e.kind) for e in await self._evidence(db, applied.id)]
            new_score = score_evidence(all_ev, cfg)
            applied.score, applied.band = new_score.score, new_score.band
            return FactOutcome(applied, "applied", changed=False, reason="corroborated existing value")

        current = applied.value if applied is not None else _current(entity, field_name)
        writable = self.writable(model, field_name)
        human_locked = applied is not None and applied.decided_by is not None and applied.method == "human"

        status, reason = "proposed", ""
        if human:
            status, reason = "applied", "entered by a team member"
        elif current in (None, "", [], {}) or _same(current, value):
            if _BAND_ORDER[score.band] >= _BAND_ORDER["probable"] or _same(current, value):
                status, reason = "applied", "fills an empty field" if not _same(current, value) else "matches CRM value"
            else:
                reason = f"band {score.band} too weak to fill a field automatically"
        elif human_locked:
            reason = "existing value was entered by a team member"
        elif _BAND_ORDER[score.band] >= _BAND_ORDER[cfg.overwrite_min_band]:
            status, reason = "applied", f"{score.band} evidence replaces previous value"
        else:
            reason = f"conflicts with current value; {score.band} evidence is below overwrite threshold"

        if status == "applied" and field_name in _IDENTITY_KEYS.get(entity_type, set()) and not _same(current, value):
            if not await self._identity_free(db, entity, field_name, value):
                status, reason = "proposed", f"another {entity_type} already owns this {field_name}"

        fact = Fact(
            tenant_id=entity.tenant_id, entity_type=entity_type, entity_id=entity.id, field=field_name,
            value=value, score=score.score, band=score.band, status=status,
            method="human" if human else method, rationale=rationale or reason,
            source_task_id=source_task_id,
            decided_by=decided_by if human else None,
            decided_at=datetime.now(timezone.utc) if human else None,
        )
        changed = False
        if status == "applied":
            changed = await self._apply(db, entity, fact, applied, writable)
        else:
            db.add(fact)
            await db.flush()
        await self._add_evidence(db, fact, evidence)
        return FactOutcome(fact, status, changed=changed, reason=reason)

    async def _apply(self, db, entity, fact: Fact, previous: Optional[Fact], writable: bool) -> bool:
        now = datetime.now(timezone.utc)
        if previous is not None:
            previous.status = "superseded"
            previous.superseded_at = now
            await db.flush()  # free the "one applied fact per field" slot first
        db.add(fact)
        await db.flush()
        if previous is not None:
            previous.superseded_by = fact.id
        changed = False
        if writable:
            new = _column_value(type(entity), fact.field, fact.value)
            if getattr(entity, fact.field) != new:
                setattr(entity, fact.field, new)
                changed = True
        return changed

    async def _identity_free(self, db, entity, field_name, value) -> bool:
        model = type(entity)
        stmt = select(model.id).where(model.tenant_id == entity.tenant_id, getattr(model, field_name) == value,
                                      model.id != entity.id).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none() is None

    async def _add_evidence(self, db, fact: Fact, evidence: list[EvidenceInput]) -> None:
        cfg = self._cfg(fact.tenant_id)
        for ev in evidence:
            kind = cfg.evidence_kinds[ev.kind]
            db.add(FactEvidence(tenant_id=fact.tenant_id, fact_id=fact.id, kind=ev.kind, weight=kind.weight,
                                primary=kind.primary, detail=ev.detail, source_url=ev.source_url,
                                source_ref=ev.source_ref, observed_at=ev.observed_at or datetime.now(timezone.utc)))
        await db.flush()

    async def _evidence(self, db, fact_id) -> list[FactEvidence]:
        return list((await db.execute(select(FactEvidence).where(FactEvidence.fact_id == fact_id))).scalars().all())

    # ── review ───────────────────────────────────────────────────────────────

    async def accept(self, db: AsyncSession, fact_id: uuid.UUID, reviewer: str) -> Fact:
        """A reviewer accepts a proposed fact: it becomes applied (with human evidence)."""
        fact = await db.get(Fact, fact_id)
        if fact is None or fact.status != "proposed":
            raise ValueError("only proposed facts can be accepted")
        entity = await db.get(ENTITY_MODELS[fact.entity_type], fact.entity_id)
        outcome = await self.record(db, entity, fact.field, fact.value,
                                    [EvidenceInput(kind="human_entered", detail=f"accepted proposal {fact.id}")],
                                    rationale=f"accepted by {reviewer}", decided_by=reviewer)
        fact.status, fact.superseded_by, fact.superseded_at = "superseded", outcome.fact.id, datetime.now(timezone.utc)
        fact.decided_by, fact.decided_at = reviewer, datetime.now(timezone.utc)
        await db.flush()
        return outcome.fact

    async def dismiss(self, db: AsyncSession, fact_id: uuid.UUID, reviewer: str, note: str = "") -> Fact:
        fact = await db.get(Fact, fact_id)
        if fact is None or fact.status != "proposed":
            raise ValueError("only proposed facts can be dismissed")
        fact.status, fact.decided_by, fact.decided_at = "dismissed", reviewer, datetime.now(timezone.utc)
        if note:
            fact.rationale = f"{fact.rationale or ''}\nDismissed: {note}".strip()
        await db.flush()
        return fact

    # ── reads ────────────────────────────────────────────────────────────────

    async def facts_for(self, db: AsyncSession, entity_type: str, entity_id: uuid.UUID,
                        statuses: tuple[str, ...] = ("applied", "proposed")) -> list[dict]:
        rows = (await db.execute(select(Fact).where(Fact.entity_type == entity_type, Fact.entity_id == entity_id,
                                                    Fact.status.in_(statuses))
                                 .order_by(Fact.field, Fact.created_at.desc()))).scalars().all()
        out = []
        for f in rows:
            ev = await self._evidence(db, f.id)
            out.append({
                "id": str(f.id), "field": f.field, "value": f.value, "status": f.status, "band": f.band,
                "score": f.score, "method": f.method, "rationale": f.rationale,
                "observed_at": f.observed_at.isoformat(),
                "evidence": [{"kind": e.kind, "weight": e.weight, "primary": e.primary, "detail": e.detail,
                              "source_url": e.source_url, "source_ref": e.source_ref,
                              "observed_at": e.observed_at.isoformat()} for e in ev],
            })
        return out
