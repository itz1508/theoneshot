"""A-Flow Fulfilment — automated gap resolution coordinator.

This package provides the fulfilment coordinator that bridges between
A-Flow gap detection and gap closure. It classifies findings, routes them
to appropriate participants, and produces revised plans with zero remaining
gaps where possible.
"""

from .coordinator import FulfilmentCoordinator
from .registry import FulfilmentParticipant, FulfilmentResult, ParticipantRegistry

__all__ = [
    "FulfilmentCoordinator",
    "FulfilmentResult",
    "FulfilmentParticipant",
    "ParticipantRegistry",
]
