"""
Tests for provider interfaces and mock implementations.

These tests verify:
- Mock providers implement the LeadSourceProvider interface
- Records have expected types and fields
- Search filtering works correctly
- Provider registry resolves names correctly
- Unknown provider raises ProviderError
"""

import pytest

from app.integrations.lead_source import ProviderError
from app.integrations.lead_source_record import LeadSearchCriteria, LeadSourceRecord
from app.integrations.mock_apollo import MockApolloProvider
from app.integrations.mock_scout import MockScoutProvider
from app.integrations.registry import ProviderRegistry, provider_registry


class TestLeadSourceRecord:
    def test_collected_at_defaults_to_now(self):
        record = LeadSourceRecord(source="test", email="a@b.com")
        assert record.collected_at is not None

    def test_has_email_true(self):
        record = LeadSourceRecord(source="test", email="a@b.com")
        assert record.has_email() is True

    def test_has_email_false_when_none(self):
        record = LeadSourceRecord(source="test")
        assert record.has_email() is False

    def test_has_email_false_when_blank(self):
        record = LeadSourceRecord(source="test", email="   ")
        assert record.has_email() is False

    def test_has_company_domain_true(self):
        record = LeadSourceRecord(source="test", company_domain="example.com")
        assert record.has_company_domain() is True

    def test_has_company_domain_false(self):
        record = LeadSourceRecord(source="test")
        assert record.has_company_domain() is False

    def test_effective_first_name_from_first_name(self):
        record = LeadSourceRecord(source="test", first_name="Alice", full_name="Alice Chen")
        assert record.effective_first_name() == "Alice"

    def test_effective_first_name_from_full_name(self):
        record = LeadSourceRecord(source="test", full_name="Alice Chen")
        assert record.effective_first_name() == "Alice"

    def test_effective_first_name_fallback(self):
        record = LeadSourceRecord(source="test")
        assert record.effective_first_name() == "Unknown"

    def test_effective_last_name_from_last_name(self):
        record = LeadSourceRecord(source="test", last_name="Chen")
        assert record.effective_last_name() == "Chen"

    def test_effective_last_name_from_full_name(self):
        record = LeadSourceRecord(source="test", full_name="Alice Chen")
        assert record.effective_last_name() == "Chen"

    def test_effective_last_name_none_when_single_name(self):
        record = LeadSourceRecord(source="test", full_name="Alice")
        assert record.effective_last_name() is None


class TestMockApolloProvider:
    @pytest.mark.anyio
    async def test_provider_name(self):
        p = MockApolloProvider()
        assert p.provider_name == "mock_apollo"

    @pytest.mark.anyio
    async def test_returns_list(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria())
        assert isinstance(records, list)

    @pytest.mark.anyio
    async def test_returns_three_records_unfiltered(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria())
        assert len(records) == 3

    @pytest.mark.anyio
    async def test_all_records_are_lead_source_records(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria())
        for r in records:
            assert isinstance(r, LeadSourceRecord)

    @pytest.mark.anyio
    async def test_source_is_provider_name(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria())
        for r in records:
            assert r.source == "mock_apollo"

    @pytest.mark.anyio
    async def test_filter_by_domain(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria(company_domain="quantum.io"))
        assert len(records) == 1
        assert records[0].company_domain == "quantum.io"
        assert records[0].email == "alice.chen@quantum.io"

    @pytest.mark.anyio
    async def test_filter_by_unknown_domain_returns_empty(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria(company_domain="doesnotexist.xyz"))
        assert records == []

    @pytest.mark.anyio
    async def test_limit_respected(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria(limit=1))
        assert len(records) == 1

    @pytest.mark.anyio
    async def test_all_records_have_email(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria())
        for r in records:
            assert r.email is not None

    @pytest.mark.anyio
    async def test_raw_data_preserved(self):
        p = MockApolloProvider()
        records = await p.search_leads(LeadSearchCriteria())
        for r in records:
            assert r.raw_data is not None
            assert isinstance(r.raw_data, dict)

    @pytest.mark.anyio
    async def test_health_check_ok(self):
        p = MockApolloProvider()
        result = await p.health_check()
        assert result["status"] == "ok"


class TestMockScoutProvider:
    @pytest.mark.anyio
    async def test_provider_name(self):
        p = MockScoutProvider()
        assert p.provider_name == "mock_scout"

    @pytest.mark.anyio
    async def test_returns_three_records_unfiltered(self):
        p = MockScoutProvider()
        records = await p.search_leads(LeadSearchCriteria())
        assert len(records) == 3

    @pytest.mark.anyio
    async def test_source_is_provider_name(self):
        p = MockScoutProvider()
        records = await p.search_leads(LeadSearchCriteria())
        for r in records:
            assert r.source == "mock_scout"

    @pytest.mark.anyio
    async def test_overlapping_domain_with_apollo(self):
        """quantum.io appears in both Apollo and Scout (different people)."""
        apollo = MockApolloProvider()
        scout = MockScoutProvider()
        apollo_records = await apollo.search_leads(LeadSearchCriteria(company_domain="quantum.io"))
        scout_records = await scout.search_leads(LeadSearchCriteria(company_domain="quantum.io"))
        assert len(apollo_records) == 1
        assert len(scout_records) == 1
        # Same company, different person
        assert apollo_records[0].company_domain == scout_records[0].company_domain
        assert apollo_records[0].email != scout_records[0].email

    @pytest.mark.anyio
    async def test_overlapping_email_with_apollo(self):
        """m.rivera@horizon.ai appears in both Apollo and Scout."""
        apollo = MockApolloProvider()
        scout = MockScoutProvider()
        apollo_records = await apollo.search_leads(LeadSearchCriteria(company_domain="horizon.ai"))
        scout_records = await scout.search_leads(LeadSearchCriteria(company_domain="horizon.ai"))
        assert apollo_records[0].email == scout_records[0].email == "m.rivera@horizon.ai"


class TestProviderRegistry:
    def test_registered_providers_include_mocks(self):
        providers = provider_registry.list_providers()
        assert "mock_apollo" in providers
        assert "mock_scout" in providers

    def test_get_mock_apollo(self):
        p = provider_registry.get("mock_apollo")
        assert p.provider_name == "mock_apollo"

    def test_get_mock_scout(self):
        p = provider_registry.get("mock_scout")
        assert p.provider_name == "mock_scout"

    def test_unknown_provider_raises(self):
        with pytest.raises(ProviderError) as exc_info:
            provider_registry.get("nonexistent_provider")
        assert "nonexistent_provider" in str(exc_info.value)

    def test_custom_registry_register_and_get(self):
        reg = ProviderRegistry()
        apollo = MockApolloProvider()
        reg.register(apollo)
        assert reg.get("mock_apollo").provider_name == "mock_apollo"

    def test_custom_registry_unknown_raises(self):
        reg = ProviderRegistry()
        with pytest.raises(ProviderError):
            reg.get("not_here")

    def test_list_providers_sorted(self):
        providers = provider_registry.list_providers()
        assert providers == sorted(providers)
