"""
Abstract interfaces and contracts for the Oxygen layer.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.oxygen.models import Decision, OxygenContext


class DecisionEngine(ABC):
    """
    Abstract interface for decision engines in Oxygen.

    A decision engine takes an immutable OxygenContext and returns an explicit
    Decision indicating what action should be taken. It must not execute the action
    or mutate the database.
    """

    @abstractmethod
    def decide(self, context: OxygenContext) -> Decision:
        """
        Evaluate context and return an actionable Decision.

        Args:
            context: Read-only state of the lead, entities, events, and tasks.

        Returns:
            Decision instance defining the recommended action.
        """
        ...


@dataclass
class CapabilityContract:
    """
    Contract describing a capability registered with the Oxygen registry.
    Oxygen knows WHAT capabilities exist without knowing the underlying implementation.
    """
    name: str
    description: str
    input_contract: dict[str, Any] = field(default_factory=dict)
    is_available: bool = True
