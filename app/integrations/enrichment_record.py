"""
Normalized enrichment result models.

These dataclasses carry enrichment data returned by enrichment providers.
They are provider-agnostic — all concrete providers map their API responses
into these structures before returning them.

Design:
- All fields are optional except the deduplication key (domain / email).
- found=False means the provider has no data; fields will be None.
- source tracks which provider produced the data.
- Do NOT invent values when a provider does not supply them.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompanyEnrichmentData:
    """
    Structured enrichment data for a Company.

    All fields are optional — providers only populate what they know.
    """

    # Core identity
    name: Optional[str] = None
    domain: Optional[str] = None

    # Firmographic
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    country: Optional[str] = None
    city: Optional[str] = None

    # Web presence
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    description: Optional[str] = None

    # Technographic / financial signals
    technologies: list[str] = field(default_factory=list)
    funding_stage: Optional[str] = None
    revenue_range: Optional[str] = None


@dataclass
class CompanyEnrichmentResult:
    """
    Full result of a company enrichment attempt.

    Attributes:
        found:       True if the provider has data for this domain.
        domain:      The normalized domain that was queried.
        data:        Enrichment payload (None when found=False).
        source:      Provider name.
        external_id: Provider's own identifier for this company.
    """

    found: bool
    domain: str
    source: str
    data: Optional[CompanyEnrichmentData] = None
    external_id: Optional[str] = None


@dataclass
class PersonEnrichmentData:
    """
    Structured enrichment data for a Person.

    All fields are optional — providers only populate what they know.
    """

    # Name
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    # Contact
    email: Optional[str] = None
    phone: Optional[str] = None

    # Professional
    job_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    seniority: Optional[str] = None
    department: Optional[str] = None

    # Location
    location: Optional[str] = None


@dataclass
class PersonEnrichmentResult:
    """
    Full result of a person enrichment attempt.

    Attributes:
        found:       True if the provider has data for this email.
        email:       The normalized email that was queried.
        data:        Enrichment payload (None when found=False).
        source:      Provider name.
        external_id: Provider's own identifier for this person.
    """

    found: bool
    email: str
    source: str
    data: Optional[PersonEnrichmentData] = None
    external_id: Optional[str] = None
