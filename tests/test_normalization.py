"""
Unit tests for normalization utilities.

All tests are pure — no I/O, no DB, no mocks needed.
"""

import pytest

from app.services.normalization import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_url,
)


class TestEmailNormalization:
    def test_lowercase(self):
        assert normalize_email("Alice@EXAMPLE.COM") == "alice@example.com"

    def test_strips_whitespace(self):
        assert normalize_email("  alice@example.com  ") == "alice@example.com"

    def test_strips_and_lowercases(self):
        assert normalize_email("  ALICE@Example.Com  ") == "alice@example.com"

    def test_none_returns_none(self):
        assert normalize_email(None) is None

    def test_blank_returns_none(self):
        assert normalize_email("") is None
        assert normalize_email("   ") is None

    def test_normal_email_unchanged(self):
        assert normalize_email("alice@example.com") == "alice@example.com"

    def test_subdomain_email(self):
        assert normalize_email("User@mail.Company.IO") == "user@mail.company.io"


class TestDomainNormalization:
    def test_removes_https_prefix(self):
        assert normalize_domain("https://example.com") == "example.com"

    def test_removes_http_prefix(self):
        assert normalize_domain("http://example.com") == "example.com"

    def test_removes_www(self):
        assert normalize_domain("www.example.com") == "example.com"

    def test_removes_https_and_www(self):
        assert normalize_domain("https://www.example.com") == "example.com"

    def test_removes_trailing_slash(self):
        assert normalize_domain("example.com/") == "example.com"
        assert normalize_domain("https://example.com/") == "example.com"

    def test_removes_path_after_domain(self):
        assert normalize_domain("example.com/about") == "example.com"
        assert normalize_domain("https://example.com/about/us") == "example.com"

    def test_lowercases(self):
        assert normalize_domain("QUANTUM.IO") == "quantum.io"
        assert normalize_domain("https://Example.COM") == "example.com"

    def test_strips_whitespace(self):
        assert normalize_domain("  quantum.io  ") == "quantum.io"

    def test_none_returns_none(self):
        assert normalize_domain(None) is None

    def test_blank_returns_none(self):
        assert normalize_domain("") is None
        assert normalize_domain("   ") is None

    def test_already_normalized(self):
        assert normalize_domain("quantum.io") == "quantum.io"

    def test_complex_url(self):
        assert normalize_domain("https://www.Horizon.AI/products/ai") == "horizon.ai"


class TestNameNormalization:
    def test_strips_whitespace(self):
        assert normalize_name("  Alice  ") == "Alice"

    def test_collapses_internal_spaces(self):
        assert normalize_name("Alice   Chen") == "Alice Chen"

    def test_preserves_case(self):
        assert normalize_name("alice chen") == "alice chen"
        assert normalize_name("ALICE CHEN") == "ALICE CHEN"

    def test_none_returns_none(self):
        assert normalize_name(None) is None

    def test_blank_returns_none(self):
        assert normalize_name("") is None
        assert normalize_name("   ") is None

    def test_single_name(self):
        assert normalize_name("Priya") == "Priya"

    def test_tabs_and_newlines_collapsed(self):
        assert normalize_name("Alice\tChen") == "Alice Chen"
        assert normalize_name("Alice\nChen") == "Alice Chen"


class TestUrlNormalization:
    def test_strips_whitespace(self):
        assert normalize_url("  https://linkedin.com/in/alice  ") == "https://linkedin.com/in/alice"

    def test_none_returns_none(self):
        assert normalize_url(None) is None

    def test_blank_returns_none(self):
        assert normalize_url("") is None

    def test_lowercases_host_only(self):
        result = normalize_url("https://LinkedIn.COM/in/AliceChen")
        assert result == "https://linkedin.com/in/AliceChen"

    def test_http_lowercases_host(self):
        result = normalize_url("http://Example.COM/page")
        assert result == "http://example.com/page"


class TestPhoneNormalization:
    def test_strips_whitespace(self):
        assert normalize_phone("  +1-555-0101  ") == "+1-555-0101"

    def test_none_returns_none(self):
        assert normalize_phone(None) is None

    def test_blank_returns_none(self):
        assert normalize_phone("") is None

    def test_preserves_format(self):
        assert normalize_phone("+1 (555) 010-1234") == "+1 (555) 010-1234"
