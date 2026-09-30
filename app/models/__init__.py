"""
Models package.

Import all ORM models here so Alembic's autogenerate can discover them
when it imports this package via env.py.
"""

from app.models.base import (  # noqa: F401
    Base,
    Company,
    Event,
    Interaction,
    Lead,
    Person,
    Task,
)

__all__ = [
    "Base",
    "Company",
    "Event",
    "Interaction",
    "Lead",
    "Person",
    "Task",
]
