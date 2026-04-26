"""
Deterministic temporal context for Curator prompts.

The LLM is bad at time math — it guesses whether to say "Good morning" or
"Good afternoon", and it recalculates durations incorrectly. This module
pre-computes every time-related value so the LLM only has to copy text.

Usage:
    temporal = TemporalContext(user_tz="America/Chicago")
    block = temporal.for_prompt(last_seen_utc)
    # Prepend `block` to any Curator prompt.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class TemporalContext:
    """Deterministic time context — the LLM never does time math."""

    def __init__(self, user_tz: str = "America/Chicago"):
        self.tz = ZoneInfo(user_tz)
        # Always compute "now" in the user's local timezone
        self.now = datetime.now(self.tz)

    @property
    def greeting_hint(self) -> str:
        """Return the appropriate greeting word for the current local time."""
        hour = self.now.hour
        if hour < 12:
            return "morning"
        elif hour < 17:
            return "afternoon"
        return "evening"

    def _normalize(self, dt: datetime) -> datetime:
        """
        Ensure dt has timezone info.

        Naive datetimes from the database are assumed to be UTC and are
        converted to the user's local timezone for correct elapsed-time math.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz)

    def classify_session(self, last_seen: datetime) -> str:
        """Classify the return session type. Never let the LLM compute this."""
        local_last = self._normalize(last_seen)
        elapsed = self.now - local_last
        hours = elapsed.total_seconds() / 3600

        if hours < 0.17:
            return "continuation (you just stepped away)"
        elif hours < 4:
            return "same-session return"
        elif hours < 12:
            return f"same-day return ({self.format_duration(elapsed)} away)"
        elif hours < 36:
            return "next-day return (overnight)"
        else:
            return f"extended absence ({self.format_duration(elapsed)})"

    def format_duration(self, td: timedelta) -> str:
        """Human-readable duration. Pre-computed, never LLM-computed."""
        total_seconds = int(td.total_seconds())
        if total_seconds < 0:
            return "just now"
        days = total_seconds // 86400
        remaining = total_seconds % 86400
        hours = remaining // 3600
        mins = (remaining % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h" if hours else f"{days}d"
        if hours > 0:
            return f"{hours}h {mins}m" if mins else f"{hours}h"
        return f"{mins}m" if mins else "just now"

    def duration_from(self, last_seen: datetime) -> str:
        """Convenience: format elapsed time since last_seen."""
        local_last = self._normalize(last_seen)
        elapsed = self.now - local_last
        return self.format_duration(elapsed)

    def for_prompt(self, last_seen: datetime) -> str:
        """
        Full temporal block to prepend to any Curator prompt.

        Gives the LLM everything it needs: exact local time, greeting word,
        session classification, and exact duration — with instructions not
        to recalculate.
        """
        local_last = self._normalize(last_seen)
        elapsed = self.now - local_last
        duration = self.format_duration(elapsed)

        return f"""<temporal_context>
Current time: {self.now.strftime('%A, %B %d, %Y at %I:%M %p %Z')}
Day context: {self.now.strftime('%A')}, week {self.now.isocalendar()[1]}, Q{(self.now.month - 1) // 3 + 1}
Session type: {self.classify_session(last_seen)}
Appropriate greeting: Good {self.greeting_hint}
Time away: {duration} (EXACT — do not recalculate or rephrase)
</temporal_context>"""
