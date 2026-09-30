"""
Deterministic normalization utilities for lead data.

All normalization is pure (no I/O, no randomness) so it can be unit-tested
directly without any mocks.

Rules:
- Functions never raise — they return None if input is None/blank.
- Normalization is applied before identity resolution and persistence.
- Do not use regex here unless truly necessary — keep it readable.

Usage::

    from app.services.normalization import normalize_email, normalize_domain
    email = normalize_email("  Alice@EXAMPLE.COM  ")  # "alice@example.com"
    domain = normalize_domain("https://Example.com/")  # "example.com"
"""

from typing import Optional


# ── Email ─────────────────────────────────────────────────────────────────────

def normalize_email(email: Optional[str]) -> Optional[str]:
    """
    Normalize an email address for deduplication.

    Applies:
    - Strip leading/trailing whitespace
    - Lowercase the entire address

    Does NOT validate the email format — that is the schema's job.

    Returns None if input is None or blank after stripping.
    """
    if not email:
        return None
    normalized = email.strip().lower()
    return normalized if normalized else None


# ── Domain ────────────────────────────────────────────────────────────────────

def normalize_domain(domain: Optional[str]) -> Optional[str]:
    """
    Normalize a company domain for deduplication.

    Applies:
    - Strip leading/trailing whitespace
    - Lowercase
    - Remove common protocol prefixes: http://, https://, www.
    - Remove trailing slash(es)
    - Remove trailing path components (keeps only the bare domain)

    Examples:
        "https://Example.com/"  -> "example.com"
        "http://www.Acme.com"   -> "acme.com"
        "  QUANTUM.IO  "        -> "quantum.io"
        "horizon.ai/about"      -> "horizon.ai"

    Returns None if input is None or blank after stripping.
    """
    if not domain:
        return None

    d = domain.strip().lower()

    # Remove protocol prefixes
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]

    # Remove www. prefix
    if d.startswith("www."):
        d = d[4:]

    # Remove trailing slash(es)
    d = d.rstrip("/")

    # Remove path component — keep only the bare domain
    # e.g. "example.com/about" -> "example.com"
    if "/" in d:
        d = d.split("/")[0]

    return d if d else None


# ── Names ─────────────────────────────────────────────────────────────────────

def normalize_name(name: Optional[str]) -> Optional[str]:
    """
    Normalize a personal or company name.

    Applies:
    - Strip leading/trailing whitespace
    - Collapse multiple internal spaces to single space

    Does NOT change case — provider-supplied casing is preserved.

    Returns None if input is None or blank after stripping.
    """
    if not name:
        return None
    normalized = " ".join(name.split())  # collapses all whitespace
    return normalized if normalized else None


# ── URLs ──────────────────────────────────────────────────────────────────────

def normalize_url(url: Optional[str]) -> Optional[str]:
    """
    Light URL normalization.

    Applies:
    - Strip leading/trailing whitespace
    - Lowercase scheme and host (preserves path case)

    Does NOT perform full URL canonicalization.

    Returns None if input is None or blank after stripping.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    # Only normalize if it looks like a URL
    for prefix in ("http://", "https://"):
        if url.lower().startswith(prefix):
            # Lowercase scheme + host, preserve rest
            rest = url[len(prefix):]
            slash_pos = rest.find("/")
            if slash_pos == -1:
                return prefix + rest.lower()
            host = rest[:slash_pos].lower()
            path = rest[slash_pos:]
            return prefix + host + path
    return url


# ── Phone ─────────────────────────────────────────────────────────────────────

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Light phone normalization.

    Applies:
    - Strip leading/trailing whitespace

    Does NOT format or validate phone numbers — that is out of scope
    for the ingestion layer.

    Returns None if input is None or blank after stripping.
    """
    if not phone:
        return None
    normalized = phone.strip()
    return normalized if normalized else None
