"""
Integrations package — provider interfaces and implementations.

Step 2 implements:
    LeadSourceProvider      — abstract interface (lead_source.py)
    LeadSourceRecord        — normalized data model (lead_source_record.py)
    LeadSearchCriteria      — flexible query model (lead_source_record.py)
    MockApolloProvider      — deterministic mock (mock_apollo.py)
    MockScoutProvider       — deterministic mock with overlap (mock_scout.py)
    ProviderRegistry        — name-to-instance mapping (registry.py)

Design contract:
    - The ingestion service depends ONLY on LeadSourceProvider and LeadSourceRecord.
    - It never imports Apollo/Scout-specific code.
    - Adding a new provider: implement LeadSourceProvider, register in registry.py.
    - No other file needs to change.

Future providers (NOT YET IMPLEMENTED):
    ApolloProvider          — real Apollo.io API
    ScoutProvider           — real Scout API
    WebSearchProvider       — web-scraping based source
    ManualImportProvider    — CSV / webhook manual import
"""
