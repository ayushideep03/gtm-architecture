"""Test helpers: make an external action legitimately executable (consent + human approval)."""

import json

from app.compliance.approvals import payload_hash
from app.models.base import Lead, Person, Task
from app.models.memory import Approval, ContactConsent


async def make_contactable(session, lead: Lead, email: str = None) -> Person:
    """Ensure the lead has a person with an email and an allowed lawful basis."""
    person = await session.get(Person, lead.person_id) if lead.person_id else None
    if person is None:
        person = Person(first_name="Test", email=email or f"contact-{lead.id.hex[:8]}@example.test")
        session.add(person)
        await session.flush()
        lead.person_id = person.id
    session.add(ContactConsent(person_id=person.id, basis="legitimate_interest", source="test"))
    await session.flush()
    return person


async def approve(session, task: Task, reviewer: str = "reviewer@test") -> Approval:
    """Record an approved approval bound to the task's exact payload."""
    payload = json.loads(task.payload) if task.payload else {}
    approval = Approval(task_id=task.id, lead_id=task.lead_id, capability=task.task_type, status="approved",
                        original_payload=payload, final_payload=payload, payload_hash=payload_hash(payload),
                        decided_by=reviewer)
    session.add(approval)
    await session.flush()
    return approval
