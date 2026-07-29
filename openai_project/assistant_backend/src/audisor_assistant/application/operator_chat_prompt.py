"""Operator-chat system prompt — built at request time.

The prompt is assembled fresh on every /v1/chat request so it always
carries the current calendar date.  The clock and timezone are injected
so tests can be fully deterministic.

**Contract:** The server owns this system prompt.  Clients cannot supply,
override, or extend it through the request body.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone as _stdlib_tz
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("audisor_assistant")

_TIMEZONE_VAR = "AUDISOR_OPERATOR_TIMEZONE"

# On Windows without the tzdata package, ZoneInfo("UTC") may raise.
# Use stdlib UTC as the reliable fallback.
try:
    _UTC_ZONE: ZoneInfo | None = ZoneInfo("UTC")
except (ZoneInfoNotFoundError, KeyError):
    _UTC_ZONE = None


def _utc_tz():
    """Return the best available UTC timezone info."""
    return _UTC_ZONE if _UTC_ZONE is not None else _stdlib_tz.utc


def resolve_operator_timezone():
    """Resolve the configured IANA timezone, validated with :mod:`zoneinfo`.

    Returns a timezone object for the configured value when it is a valid IANA
    identifier.  Logs a warning and returns UTC on any of:

    - variable unset or empty;
    - value is not a valid IANA timezone (e.g. ``PST``, ``local``,
      ``America/LosAngeles``).
    """
    raw = os.environ.get(_TIMEZONE_VAR, "").strip()
    if not raw:
        return _utc_tz()
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, KeyError):
        logger.warning(
            "invalid_operator_timezone",
            extra={"configured": raw, "fallback": "UTC"},
        )
        return _utc_tz()


class OperatorChatClock(Protocol):
    """Abstraction over wall-clock time for the operator-chat prompt.

    The contract is:

    - ``now()`` returns the current wall-clock instant converted to the
      operator’s configured timezone so that ``strftime`` output matches
      the timezone label.
    - ``timezone_name()`` returns the IANA identifier that labels the
      datetime returned by ``now()``.

    Implementations MUST ensure the datetime and the label are consistent:
    ``now().tzinfo`` must correspond to ``timezone_name()``.
    """

    def now(self) -> datetime:
        """Current datetime in the operator’s timezone."""
        ...

    def timezone_name(self) -> str:
        """IANA timezone identifier matching the datetime from ``now()``."""
        ...


class DefaultOperatorChatClock:
    """Production clock: real wall-clock time in the validated timezone.

    The timezone is resolved from ``AUDISOR_OPERATOR_TIMEZONE`` on every
    call — never cached at module load — so hot-reconfiguration propagates
    without restarts.  Invalid values fall back to UTC with a logged warning.
    """

    def now(self) -> datetime:
        tz = resolve_operator_timezone()
        return datetime.now(tz)

    def timezone_name(self) -> str:
        return str(resolve_operator_timezone())


#: Singleton default clock used by production app instances.
default_clock: OperatorChatClock = DefaultOperatorChatClock()


_BASE_IDENTITY = "You are a helpful assistant."

_NO_GUESS_INSTRUCTION = (
    "You do not have access to live external data. Do not guess, fabricate, "
    "or speculate about current weather, news headlines, stock prices, "
    "sports scores, schedules, repository state, file contents, or any other "
    "time-sensitive facts. When the user asks for information that requires "
    "live data and no tool is connected to provide it, state clearly that "
    "live data is unavailable rather than producing a plausible-sounding "
    "but potentially incorrect answer."
)


def build_operator_chat_system_prompt(
    clock: OperatorChatClock,
    *,
    tools_available: bool = False,
) -> str:
    """Assemble the full operator-chat system prompt at request time.

    Includes:
    - the assistant identity;
    - the current calendar date (from the injected clock);
    - the configured user timezone;
    - an instruction not to guess live/time-sensitive facts;
    - a tool-availability declaration built from runtime capabilities.

    ``tools_available`` controls whether the prompt declares tools present
    or absent.  Currently always ``False``; when the toolkit is wired, the
    caller passes ``True`` and the no-tool wording is replaced.

    The prompt is built fresh on every call — never cached — so the date
    is always current and tests can inject a fixed clock.
    """
    now = clock.now()
    tz_name = clock.timezone_name()

    date_line = f"Today's date is {now.strftime('%A, %B %d, %Y')} ({tz_name})."

    # Build the tool-availability paragraph from runtime state.
    if tools_available:
        tool_line = (
            "You have access to connected tools. Use them when the user's "
            "request requires live or external data."
        )
    else:
        tool_line = _NO_GUESS_INSTRUCTION

    return "\n\n".join([
        _BASE_IDENTITY,
        date_line,
        tool_line,
    ])
