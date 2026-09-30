"""
Context builder for Oxygen.

Separates CRM and Event state retrieval from decision making.
Produces an immutable, normalized OxygenContext for a given lead.
"""

from typing import Optional
import uuid

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Company, Event, Interaction, Lead, Person, Task
from app.oxygen.models import OxygenContext


class OxygenContextBuilder:
    """
    Assembles OxygenContext from persistent CRM entities and append-only events.
    Does NOT modify the database.
    """

    async def build(self, db: AsyncSession, lead_id: uuid.UUID) -> Optional[OxygenContext]:
        """
        Build an OxygenContext for the given lead.

        Args:
            db: Active async session.
            lead_id: UUID of the Lead.

        Returns:
            OxygenContext populated with lead, person, company, events, tasks,
            and interactions, or None if the lead does not exist.
        """
        # 1. Fetch Lead
        lead_stmt = select(Lead).where(Lead.id == lead_id)
        lead_res = await db.execute(lead_stmt)
        lead = lead_res.scalar_one_or_none()
        if lead is None:
            return None

        # 2. Fetch Person if linked
        person: Optional[Person] = None
        company: Optional[Company] = None
        if lead.person_id:
            person_stmt = select(Person).where(Person.id == lead.person_id)
            person_res = await db.execute(person_stmt)
            person = person_res.scalar_one_or_none()

            # 3. Fetch Company if person is linked to one
            if person and person.company_id:
                company_stmt = select(Company).where(Company.id == person.company_id)
                company_res = await db.execute(company_stmt)
                company = company_res.scalar_one_or_none()

        # 4. Fetch Events for this lead (ordered newest to oldest)
        event_stmt = (
            select(Event)
            .where(Event.lead_id == lead_id)
            .order_by(desc(Event.occurred_at))
        )
        event_res = await db.execute(event_stmt)
        events = list(event_res.scalars().all())

        # 5. Fetch Tasks for this lead
        task_stmt = (
            select(Task)
            .where(Task.lead_id == lead_id)
            .order_by(desc(Task.created_at))
        )
        task_res = await db.execute(task_stmt)
        tasks = list(task_res.scalars().all())

        # 6. Fetch Interactions for this lead
        interaction_stmt = (
            select(Interaction)
            .where(Interaction.lead_id == lead_id)
            .order_by(desc(Interaction.occurred_at))
        )
        interaction_res = await db.execute(interaction_stmt)
        interactions = list(interaction_res.scalars().all())

        # 7. Compute enrichment flags
        has_enrichment_completed_event = any(e.event_type == "enrichment_completed" for e in events)

        is_company_enriched = False
        if company:
            is_company_enriched = (
                company.enriched_at is not None
                or any(e.event_type == "company_enriched" for e in events)
                or has_enrichment_completed_event
            )

        is_person_enriched = False
        if person:
            is_person_enriched = (
                person.enriched_at is not None
                or any(e.event_type == "person_enriched" for e in events)
                or has_enrichment_completed_event
            )

        return OxygenContext(
            lead_id=lead_id,
            lead=lead,
            person=person,
            company=company,
            events=events,
            tasks=tasks,
            interactions=interactions,
            is_company_enriched=is_company_enriched,
            is_person_enriched=is_person_enriched,
        )
