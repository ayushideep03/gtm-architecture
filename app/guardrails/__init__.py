"""
Guardrails package.

Guardrails enforce boundaries on autonomous agent behaviour before
any action is taken. They are called by the Oxygen orchestrator and
individual agents.

Planned guardrail types:
    RateLimitGuardrail      — prevent spamming a single prospect
    ContentGuardrail        — LLM-as-judge for outreach quality/tone
    ComplianceGuardrail     — GDPR/CAN-SPAM opt-out enforcement
    SpendGuardrail          — cap LLM token spend per run
    ContactFrequencyGuardrail — enforce contact frequency caps

Guardrails MUST be synchronous decisions (allow/deny + reason).
They should NOT have side effects.

A guardrail returns one of:
    GuardrailDecision.ALLOW
    GuardrailDecision.DENY(reason: str)
    GuardrailDecision.REQUIRE_HUMAN_REVIEW(reason: str)
"""
