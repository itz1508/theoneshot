from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    DRAFT = "draft"
    ANALYZED = "analyzed"
    REVISION_REQUIRED = "revision_required"
    ACCEPTED = "accepted"
    LOCKED = "locked"
    HANDED_OFF = "handed_off"
    BUILD_RETURNED = "build_returned"
    EVALUATED = "evaluated"
    INVALIDATED = "invalidated"


ALLOWED = {
    LifecycleState.DRAFT: {LifecycleState.ANALYZED},
    LifecycleState.ANALYZED: {LifecycleState.REVISION_REQUIRED, LifecycleState.ACCEPTED},
    LifecycleState.REVISION_REQUIRED: {LifecycleState.ANALYZED},
    LifecycleState.ACCEPTED: {LifecycleState.LOCKED},
    LifecycleState.LOCKED: {LifecycleState.HANDED_OFF, LifecycleState.INVALIDATED},
    LifecycleState.HANDED_OFF: {LifecycleState.BUILD_RETURNED, LifecycleState.INVALIDATED},
    LifecycleState.BUILD_RETURNED: {LifecycleState.EVALUATED, LifecycleState.INVALIDATED},
    LifecycleState.EVALUATED: set(),
    LifecycleState.INVALIDATED: {LifecycleState.ANALYZED},
}


def transition(current: LifecycleState, target: LifecycleState) -> LifecycleState:
    if target not in ALLOWED[current]:
        raise ValueError(f"invalid A-Flow lifecycle transition: {current.value} -> {target.value}")
    return target

