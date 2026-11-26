"""
Runtime GTM configuration (providers, models, selection, workflow policy, tenants).

Layering (later layers override earlier ones):
    1. config/gtm.yaml                      base, production-safe (no providers enabled)
    2. config/gtm.<profile>.yaml            profile overlay (development / test / ...)
    3. $GTM_CONFIG_FILE                     optional extra overlay (deployment-specific)

Each file may pull in other files with ``include: [relative/path.yaml, ...]``.

Secrets are never stored here. A provider lists the *names* of environment
variables it needs under ``credentials``; values are read from the environment
at load time. A provider whose credentials are missing stays registered but is
marked unavailable, so it is never selected.

The profile is taken from $GTM_PROFILE, falling back to Settings.ENVIRONMENT.
"""

from __future__ import annotations

import copy
import os
import uuid
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from app.core.tenancy import DEFAULT_TENANT_ID

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class SideEffect(str, Enum):
    """How far a capability's effects reach. Drives approval and guardrail policy."""

    READ_ONLY = "read_only"                       # reads internal or external data only
    INTERNAL_WRITE = "internal_write"             # changes our own CRM state
    EXTERNAL_COMMUNICATION = "external_communication"  # contacts a person outside the org
    SPEND = "spend"                               # incurs direct cost / purchases


# ── Provider configuration ────────────────────────────────────────────────────

class ResilienceConfig(BaseModel):
    timeout_s: float = 30.0
    max_retries: int = 2
    backoff_base_s: float = 0.5
    backoff_max_s: float = 30.0
    retry_on: list[str] = Field(
        default_factory=lambda: ["timeout", "rate_limited", "unavailable", "provider_exception"]
    )


class RateLimitConfig(BaseModel):
    requests: Optional[int] = None   # None = unlimited
    per_seconds: float = 60.0


class ProviderConfig(BaseModel):
    """One concrete integration (vendor or mock) and the capabilities it serves."""

    name: str
    factory: str                     # "package.module:ClassName"
    capabilities: list[str]
    enabled: bool = True
    mode: Literal["live", "dry_run"] = "live"
    priority: int = 50               # 0-100, higher preferred
    cost_per_call: float = 0.0       # in the org's accounting unit (e.g. USD)
    settings: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)  # init kwarg -> ENV VAR NAME
    resilience: ResilienceConfig = Field(default_factory=ResilienceConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    constraints: dict[str, list[str]] = Field(default_factory=dict)  # e.g. {"regions": ["EU"]}
    tags: list[str] = Field(default_factory=list)
    # True when the vendor de-duplicates requests by the idempotency key we send.
    # Only then may an ambiguous failure (timeout / exception) of an external
    # action be retried or failed over without risking a duplicate action.
    supports_idempotency: bool = False

    @field_validator("priority")
    @classmethod
    def _priority_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("priority must be between 0 and 100")
        return v

    def resolve_credentials(self) -> tuple[dict[str, str], list[str]]:
        """Return (resolved kwargs, missing env var names). Never logs values."""
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for kwarg, env_name in self.credentials.items():
            value = os.environ.get(env_name)
            if value:
                resolved[kwarg] = value
            else:
                missing.append(env_name)
        return resolved, missing


# ── LLM configuration ─────────────────────────────────────────────────────────

class LLMVendorConfig(BaseModel):
    """Connection details for an LLM vendor (key referenced by env var name)."""

    api_key_env: Optional[str] = None
    base_url: Optional[str] = None
    organization_env: Optional[str] = None


class LLMProfile(BaseModel):
    """A named model choice used by a role (planner, writer, researcher...)."""

    provider: str
    model: str
    temperature: Optional[float] = 0.2       # ignored by vendors/models that reject sampling params
    max_output_tokens: int = 2000
    timeout_s: float = 60.0
    effort: Optional[str] = None             # reasoning effort for vendors that support it (e.g. Anthropic)
    # Anthropic server-side refusal fallback ("default" = route by refusal category; None = off)
    refusal_fallbacks: Optional[str] = "default"
    max_retries: int = 2
    fallback_profiles: list[str] = Field(default_factory=list)


class LLMConfig(BaseModel):
    default_profile: str = "planner"
    vendors: dict[str, LLMVendorConfig] = Field(default_factory=dict)
    profiles: dict[str, LLMProfile] = Field(default_factory=dict)

    def profile(self, name: Optional[str] = None) -> LLMProfile:
        key = name or self.default_profile
        if key not in self.profiles:
            raise KeyError(f"LLM profile {key!r} is not configured")
        return self.profiles[key]


# ── Selection / workflow / capability policy ─────────────────────────────────

class SelectionWeights(BaseModel):
    priority: float = 1.0
    reliability: float = 2.0
    latency: float = 0.5
    cost: float = 0.5
    outcome_quality: float = 1.5    # learned: positive-outcome rate of a provider's actions (where outcomes exist)


class SelectionConfig(BaseModel):
    weights: SelectionWeights = Field(default_factory=SelectionWeights)
    reliability_window_days: int = 14
    reliability_prior_successes: float = 4.0
    reliability_prior_failures: float = 1.0
    latency_reference_s: float = 5.0
    cost_reference: float = 0.05
    consecutive_failure_threshold: int = 3
    failure_cooldown_s: float = 60.0
    stats_cache_ttl_s: float = 30.0
    failover: bool = True


class RepeatabilityConfig(BaseModel):
    mode: Literal["once_per_lead", "once_per_target", "repeatable"] = "repeatable"
    max_per_lead: Optional[int] = None
    cooldown_s: float = 0.0


class RetryPolicy(BaseModel):
    """Task-level retry scheduling after all providers failed with a transient error."""

    max_attempts: int = 4
    backoff_base_s: float = 30.0
    backoff_max_s: float = 3600.0
    jitter: float = 0.2          # +/- fraction applied to each delay


class CapabilityPolicy(BaseModel):
    """Config-level overrides for a capability defined in the catalogue."""

    enabled: bool = True
    description: Optional[str] = None
    requires_approval: Optional[bool] = None
    repeatability: Optional[RepeatabilityConfig] = None
    idempotency_fields: Optional[list[str]] = None
    retry: Optional[RetryPolicy] = None


class RecoveryConfig(BaseModel):
    # Side-effect classes whose interrupted (stale in_progress) tasks may be
    # resumed automatically. Anything else is flagged for human review.
    auto_resume_side_effects: list[SideEffect] = Field(
        default_factory=lambda: [SideEffect.READ_ONLY, SideEffect.INTERNAL_WRITE]
    )
    # Error codes that mean "the request may have reached the vendor".
    ambiguous_error_codes: list[str] = Field(default_factory=lambda: ["TIMEOUT", "PROVIDER_EXCEPTION"])


class ExecutionConfig(BaseModel):
    lease_s: float = 300.0       # in_progress tasks older than this are considered stale
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)


class RuntimeStateConfig(BaseModel):
    backend: Literal["redis", "memory"] = "redis"   # shared provider health / rate limits
    key_prefix: str = "gtm"
    redis_fallback_to_memory: bool = True


class LeadIdentityPolicy(BaseModel):
    terminal_statuses: list[str] = Field(
        default_factory=lambda: ["closed_won", "closed_lost", "disqualified"]
    )
    # A person never has two open leads (enforced by a unique index); a new sighting
    # of a person with an open lead is attached to it.
    # When the person only has closed leads: attach the sighting to the latest one, or open a new lead.
    on_closed_lead: Literal["attach", "create_new"] = "attach"


class IdentityConfig(BaseModel):
    # Ordered match keys; resolution stops at the first match. Exact matches only.
    company_match: list[Literal["source_record", "domain", "website", "linkedin_url", "name"]] = Field(
        default_factory=lambda: ["source_record", "domain", "website", "linkedin_url"]
    )
    person_match: list[Literal["source_record", "email", "linkedin_url", "name_and_company"]] = Field(
        default_factory=lambda: ["source_record", "email", "linkedin_url", "name_and_company"]
    )
    lead: LeadIdentityPolicy = Field(default_factory=LeadIdentityPolicy)


class ApprovalConfig(BaseModel):
    required_for_side_effects: list[SideEffect] = Field(
        default_factory=lambda: [SideEffect.EXTERNAL_COMMUNICATION]
    )
    ttl_hours: float = 72.0          # pending approvals expire after this


# ── Memory (evidence ledger) ─────────────────────────────────────────────────

class EvidenceKind(BaseModel):
    weight: float = Field(ge=0.0, le=1.0)
    primary: bool = False            # first-hand source (e.g. the person's own email reply)
    label: str = ""


class MemoryConfig(BaseModel):
    """
    Fact scoring: score = 1 - prod(1 - weight_i) over independent evidence,
    capped at 0.99; any 'contradiction' evidence caps it at contradiction_cap.
    'verified' additionally requires at least one primary source.
    (Pattern adapted from trycompai/crm, MIT.)
    """

    evidence_kinds: dict[str, EvidenceKind] = Field(default_factory=lambda: {
        "human_entered": EvidenceKind(weight=1.0, primary=True, label="Entered by a team member"),
        "email_reply": EvidenceKind(weight=0.85, primary=True, label="Stated in the prospect's own email"),
        "crm_interaction": EvidenceKind(weight=0.8, primary=True, label="Observed in our interaction history"),
        "vendor_enrichment": EvidenceKind(weight=0.85, primary=True, label="Enrichment vendor record"),
        "lead_source": EvidenceKind(weight=0.6, primary=False, label="Lead source record"),
        "web_cited_claim": EvidenceKind(weight=0.4, primary=False, label="Claim on a cited web page"),
        "llm_inference": EvidenceKind(weight=0.3, primary=False, label="Inferred by the AI from other facts"),
        "pattern_guess": EvidenceKind(weight=0.2, primary=False, label="Guessed from a naming pattern"),
        "contradiction": EvidenceKind(weight=0.0, primary=False, label="Conflicting evidence"),
    })
    verified_threshold: float = 0.85
    probable_threshold: float = 0.6
    contradiction_cap: float = 0.45
    # Only facts at or above this band may overwrite a non-empty value (human-entered values are never overwritten).
    overwrite_min_band: Literal["verified", "probable"] = "verified"
    # Evidence kind used for values from a lead source, by source-name prefix (default "lead_source").
    # e.g. {"scout": "pattern_guess"} marks scraped / guessed contact data as weak evidence.
    source_evidence_kinds: dict[str, str] = Field(default_factory=dict)
    finding_ttl_days: dict[str, int] = Field(default_factory=lambda: {
        "brief": 30, "intent_signal": 14, "news": 30, "objection": 180,
    })


# ── Compliance ───────────────────────────────────────────────────────────────

class FrequencyCap(BaseModel):
    scope: Literal["person", "domain", "company"]
    max_touches: int
    window_days: float


class LawfulBasisConfig(BaseModel):
    required: bool = True
    allowed: list[str] = Field(default_factory=lambda: ["consent", "legitimate_interest"])
    # Basis recorded automatically at ingestion (B2B legitimate interest); None = require an explicit record.
    default_on_ingest: Optional[str] = "legitimate_interest"


class ComplianceConfig(BaseModel):
    frequency_caps: list[FrequencyCap] = Field(default_factory=lambda: [
        FrequencyCap(scope="person", max_touches=2, window_days=7),
        FrequencyCap(scope="person", max_touches=4, window_days=30),
        FrequencyCap(scope="domain", max_touches=10, window_days=7),
    ])
    min_hours_between_touches: float = 48.0
    # The minimum gap protects against unsolicited follow-ups. When the prospect has replied
    # since our last touch we are answering them, so the gap is waived (caps still apply).
    waive_gap_after_reply: bool = True
    lawful_basis: LawfulBasisConfig = Field(default_factory=LawfulBasisConfig)
    daily_spend_limit: Optional[float] = None     # per tenant, summed from provider cost_per_call


class CampaignConfig(BaseModel):
    """What Oxygen is trying to achieve; read by the planner, never hardcoded in prompts."""

    name: str = "default"
    offer: str = ""
    ideal_customer_profile: str = ""
    goal: str = "Book a qualified discovery meeting"
    tone: str = "Concise, specific, respectful; no hype"
    constraints: list[str] = Field(default_factory=list)
    qualification_min_score: int = 60        # deterministic floor on top of the model's judgement


class OutreachPolicy(BaseModel):
    """Deterministic content policy applied to every AI draft before it can be proposed for sending."""

    channels: list[str] = Field(default_factory=lambda: ["email", "linkedin", "phone"])
    sender_name: str = ""
    sender_title: str = ""
    sender_company: str = ""
    max_words: dict[str, int] = Field(default_factory=lambda: {"email": 160, "linkedin": 80, "phone": 250})
    max_subject_chars: int = 90
    banned_phrases: list[str] = Field(default_factory=lambda: [
        "guarantee", "risk-free", "act now", "limited time", "100%", "as per my last email", "just checking in",
    ])
    email_footer: Optional[str] = "If this isn't relevant, just reply \"no thanks\" and I won't follow up."
    require_evidence_for_personalisation: bool = True


class ResearchPolicy(BaseModel):
    """Website research limits; research only reads the prospect company's own domain."""

    paths: list[str] = Field(default_factory=lambda: ["/", "/about", "/company", "/careers", "/blog", "/news"])
    max_pages: int = 5
    max_chars_per_page: int = 12000
    timeout_s: float = 10.0
    user_agent: str = "OxygenResearchBot/1.0 (+B2B prospect research; respects robots.txt)"
    respect_robots_txt: bool = True

class OxygenConfig(BaseModel):
    decision_engine: Literal["llm", "rules"] = "llm"
    fallback_engine: Literal["rules"] = "rules"
    llm_profile: str = "planner"
    max_actions_per_cycle: int = 1
    min_confidence: float = 0.35          # below this, an action is escalated to a human instead
    default_wait_hours: float = 72.0      # re-evaluate after this when the planner waits without a time
    max_wait_hours: float = 24.0 * 30
    context_interaction_limit: int = 12
    context_decision_limit: int = 8
    campaign: CampaignConfig = Field(default_factory=CampaignConfig)
    outreach: OutreachPolicy = Field(default_factory=OutreachPolicy)
    research: ResearchPolicy = Field(default_factory=ResearchPolicy)


class TenancyConfig(BaseModel):
    default_tenant_id: uuid.UUID = DEFAULT_TENANT_ID
    default_tenant_name: str = "Default organisation"


class TenantOverride(BaseModel):
    """Per-tenant overrides; only the keys present are applied."""

    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    capabilities: dict[str, CapabilityPolicy] = Field(default_factory=dict)
    approval: Optional[ApprovalConfig] = None
    oxygen: Optional[dict[str, Any]] = None
    identity: Optional[dict[str, Any]] = None
    compliance: Optional[dict[str, Any]] = None
    feature_flags: dict[str, bool] = Field(default_factory=dict)


class WebhookConfig(BaseModel):
    secret_env: str = "OXYGEN_WEBHOOK_SECRET"     # HMAC-SHA256 secret for X-Oxygen-Signature
    allow_unsigned: bool = False                  # development/test convenience only
    max_skew_s: float = 600.0                     # X-Oxygen-Timestamp tolerance (replay protection)


class ReplyPolicy(BaseModel):
    # Deterministic opt-out detection (case-insensitive regex). A match always suppresses the
    # contact and withdraws consent, whatever the AI classification says.
    opt_out_patterns: list[str] = Field(default_factory=lambda: [
        r"\bunsubscribe\b", r"\bremove me\b", r"\bstop (emailing|contacting|messaging)\b",
        r"\bdo not (contact|email|message)\b", r"\bdon'?t (contact|email|message) me\b",
        r"^\s*no,? thanks?\b", r"\btake me off\b", r"\bopt[- ]?out\b",
    ])
    llm_profile: str = "researcher"


class ReactionRule(BaseModel):
    work_kind: str = "evaluate"
    delay_minutes: float = 0.0
    priority: int = 5


class AutonomyConfig(BaseModel):
    enabled: bool = True                          # worker may run; pause at runtime via API (Redis flag)
    tick_interval_s: float = 15.0
    max_work_items_per_tick: int = 10
    max_tasks_per_tick: int = 20
    max_cycles_per_lead_per_day: int = 12         # hard cap on Oxygen evaluations per prospect
    lease_s: float = 300.0
    # Event type -> re-evaluate the lead. Anything not listed does not wake Oxygen.
    reactions: dict[str, ReactionRule] = Field(default_factory=lambda: {
        "lead_ingested": ReactionRule(),
        "lead_resourced": ReactionRule(delay_minutes=60),
        "task_completed": ReactionRule(),
        "task_failed": ReactionRule(delay_minutes=30),
        "approval_rejected": ReactionRule(),
        "reply_received": ReactionRule(priority=9),
        "email_bounced": ReactionRule(priority=7),
        "meeting_booked": ReactionRule(priority=8),
        "meeting_cancelled": ReactionRule(priority=8),
    })


class LeadImportConfig(BaseModel):
    # Accepted CSV header aliases -> canonical LeadSourceRecord field
    column_aliases: dict[str, list[str]] = Field(default_factory=lambda: {
        "first_name": ["first_name", "first name", "firstname", "given name"],
        "last_name": ["last_name", "last name", "lastname", "surname", "family name"],
        "full_name": ["full_name", "name", "full name"],
        "email": ["email", "email address", "work email"],
        "phone": ["phone", "phone number", "mobile"],
        "job_title": ["job_title", "title", "job title", "position"],
        "linkedin_url": ["linkedin_url", "linkedin", "linkedin url", "person linkedin url"],
        "company_name": ["company_name", "company", "organization", "account"],
        "company_domain": ["company_domain", "domain", "website", "company website"],
        "industry": ["industry"],
        "employee_count": ["employee_count", "employees", "company size", "# employees"],
        "location": ["location", "city", "country"],
        "external_id": ["external_id", "id", "record id"],
    })
    max_rows: int = 5000


class RevOpsConfig(BaseModel):
    attribution_window_days: float = 14.0          # an outcome counts for the latest touch within this window
    positive_reply_categories: list[str] = Field(default_factory=lambda: [
        "positive_interest", "meeting_request", "question", "referral"])
    negative_reply_categories: list[str] = Field(default_factory=lambda: ["not_interested", "unsubscribe"])
    employee_bands: list[int] = Field(default_factory=lambda: [50, 200, 1000, 5000])   # band upper bounds
    min_sample_for_insight: int = 5                # below this, insights are flagged as weak evidence
    min_sample_for_recommendation: int = 20        # recommendations need at least this many touches
    insight_similar_journeys: int = 3              # converted journeys shown to the planner


class GTMConfig(BaseModel):
    profile: str = "production"
    tenancy: TenancyConfig = Field(default_factory=TenancyConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    providers: list[ProviderConfig] = Field(default_factory=list)
    capabilities: dict[str, CapabilityPolicy] = Field(default_factory=dict)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    oxygen: OxygenConfig = Field(default_factory=OxygenConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    runtime: RuntimeStateConfig = Field(default_factory=RuntimeStateConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    webhooks: WebhookConfig = Field(default_factory=WebhookConfig)
    replies: ReplyPolicy = Field(default_factory=ReplyPolicy)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    lead_import: LeadImportConfig = Field(default_factory=LeadImportConfig)
    revops: RevOpsConfig = Field(default_factory=RevOpsConfig)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    tenants: dict[str, TenantOverride] = Field(default_factory=dict)

    @field_validator("providers")
    @classmethod
    def _unique_provider_names(cls, v: list[ProviderConfig]) -> list[ProviderConfig]:
        names = [p.name for p in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate provider names in config: {sorted(dupes)}")
        return v

    def provider(self, name: str) -> Optional[ProviderConfig]:
        return next((p for p in self.providers if p.name == name), None)

    def for_tenant(self, tenant_id: Optional[uuid.UUID | str]) -> "GTMConfig":
        """Return this config with the tenant's overrides applied (cached)."""
        if tenant_id is None:
            return self
        key = str(tenant_id)
        override = self.tenants.get(key)
        if override is None:
            return self
        cache = self.__dict__.setdefault("_tenant_cache", {})
        if key not in cache:
            cache[key] = _apply_tenant_override(self, override)
        return cache[key]


def _apply_tenant_override(base: GTMConfig, override: TenantOverride) -> GTMConfig:
    data = base.model_dump(mode="python")
    by_name = {p["name"]: p for p in data["providers"]}
    for name, patch in override.providers.items():
        if name not in by_name:
            raise ValueError(f"tenant override references unknown provider {name!r}")
        by_name[name] = _deep_merge(by_name[name], patch)
    data["providers"] = list(by_name.values())
    for cap, policy in override.capabilities.items():
        data["capabilities"][cap] = _deep_merge(
            data["capabilities"].get(cap, {}), policy.model_dump(exclude_unset=True)
        )
    if override.approval is not None:
        data["approval"] = override.approval.model_dump()
    if override.oxygen:
        data["oxygen"] = _deep_merge(data["oxygen"], override.oxygen)
    if override.identity:
        data["identity"] = _deep_merge(data["identity"], override.identity)
    if override.compliance:
        data["compliance"] = _deep_merge(data["compliance"], override.compliance)
    data["feature_flags"] = {**data["feature_flags"], **override.feature_flags}
    data["tenants"] = {}
    return GTMConfig.model_validate(data)


# ── Loading ───────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if key == "providers" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_provider_lists(out[key], value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _merge_provider_lists(base: list[dict], overlay: list[dict]) -> list[dict]:
    """Providers merge by name, so overlays can tweak one provider without restating it."""
    merged = {p["name"]: p for p in base}
    order = [p["name"] for p in base]
    for item in overlay:
        name = item.get("name")
        if not name:
            raise ValueError("every provider entry needs a 'name'")
        if name in merged:
            merged[name] = _deep_merge(merged[name], item)
        else:
            merged[name] = copy.deepcopy(item)
            order.append(name)
    return [merged[n] for n in order]


def _read_yaml(path: Path, seen: Optional[set[Path]] = None) -> dict:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        raise ValueError(f"circular config include: {path}")
    seen.add(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} must contain a mapping")
    includes = data.pop("include", []) or []
    merged: dict = {}
    for inc in includes:
        merged = _deep_merge(merged, _read_yaml(path.parent / inc, seen))
    return _deep_merge(merged, data)


def resolve_profile() -> str:
    explicit = os.environ.get("GTM_PROFILE")
    if explicit:
        return explicit
    from app.core.config import get_settings
    return get_settings().ENVIRONMENT


def load_gtm_config(
    profile: Optional[str] = None,
    config_dir: Optional[Path] = None,
    extra_file: Optional[Path] = None,
) -> GTMConfig:
    """Load and validate the layered configuration."""
    profile = profile or resolve_profile()
    directory = config_dir or CONFIG_DIR
    data: dict = {}
    base = directory / "gtm.yaml"
    if base.exists():
        data = _read_yaml(base)
    overlay = directory / f"gtm.{profile}.yaml"
    if overlay.exists():
        data = _deep_merge(data, _read_yaml(overlay))
    extra = extra_file or (Path(os.environ["GTM_CONFIG_FILE"]) if os.environ.get("GTM_CONFIG_FILE") else None)
    if extra is not None:
        data = _deep_merge(data, _read_yaml(extra))
    data["profile"] = profile
    return GTMConfig.model_validate(data)


@lru_cache(maxsize=1)
def get_gtm_config() -> GTMConfig:
    return load_gtm_config()


def reload_gtm_config() -> GTMConfig:
    """Clear the cache and reload (used by tests and admin reloads)."""
    get_gtm_config.cache_clear()
    return get_gtm_config()
