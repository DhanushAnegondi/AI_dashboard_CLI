"""
state.py — Immutable dataclasses for all dashboard state.

All state is defined here. Collectors produce new instances of these
dataclasses; they never mutate in place. The DashboardState singleton
is the shared object updated under a threading.Lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime


# ---------------------------------------------------------------------------
# Claude Code state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeUsage:
    """Token counts for a single Claude assistant message."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_context_tokens(self) -> int:
        """Tokens that count toward the context window fill."""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens


@dataclass(frozen=True)
class ClaudeState:
    """State for the currently active Claude Code session.

    Context fill uses the LAST message's token counts (not cumulative sums).
    Each API call includes the full conversation history in input/cache tokens,
    so summing across turns would multiply-count the same history.

    Session totals (output_tokens_total, cache_creation_total) accumulate
    across turns for session-level stats display.
    """

    session_id: str = ""
    model: str = ""
    context_window: int = 200_000

    # Latest single-turn context snapshot — used for context fill %
    # These come from the most recent assistant message's usage block.
    latest_input_tokens: int = 0
    latest_cache_read_tokens: int = 0
    latest_cache_creation_tokens: int = 0
    latest_output_tokens: int = 0

    # Cumulative session totals — used for session stats panel
    output_tokens_total: int = 0
    cache_creation_total: int = 0

    # Metadata
    session_file: str = ""
    last_updated: datetime = field(default_factory=datetime.now)

    @property
    def total_context_tokens(self) -> int:
        """Tokens filling the context window in the last turn."""
        return (
            self.latest_input_tokens
            + self.latest_cache_read_tokens
            + self.latest_cache_creation_tokens
        )

    @property
    def context_fill_pct(self) -> float:
        if self.context_window == 0:
            return 0.0
        return min(100.0, self.total_context_tokens / self.context_window * 100.0)

    @property
    def is_active(self) -> bool:
        return bool(self.session_id)


# ---------------------------------------------------------------------------
# Codex state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodexRateLimits:
    """Rate limit information from a Codex token_count event."""

    primary_used_pct: float = 0.0
    primary_window_minutes: int = 300
    primary_resets_at: int = 0  # Unix timestamp

    secondary_used_pct: float = 0.0
    secondary_window_minutes: int = 10080
    secondary_resets_at: int = 0  # Unix timestamp


@dataclass(frozen=True)
class CodexState:
    """Accumulated state for the most recent Codex session."""

    session_id: str = ""
    model: str = ""
    context_window: int = 258_400

    # From token_count events (cumulative)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    rate_limits: CodexRateLimits = field(default_factory=CodexRateLimits)

    session_file: str = ""
    last_updated: datetime = field(default_factory=datetime.now)

    @property
    def context_fill_pct(self) -> float:
        if self.context_window == 0:
            return 0.0
        return min(100.0, self.total_tokens / self.context_window * 100.0)

    @property
    def is_active(self) -> bool:
        return bool(self.session_id)


# ---------------------------------------------------------------------------
# Historical / daily stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyActivity:
    """One entry from stats-cache.json dailyActivity."""

    date: str = ""
    message_count: int = 0
    session_count: int = 0
    tool_call_count: int = 0


@dataclass(frozen=True)
class DailyModelTokens:
    """Token counts keyed by model for a single day."""

    date: str = ""
    tokens_by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens_by_model.values())


@dataclass(frozen=True)
class CodexSessionSummary:
    """One row from the Codex SQLite threads table."""

    session_id: str = ""
    model: str = ""
    tokens_used: int = 0
    created_at: int = 0  # Unix timestamp
    cwd: str = ""
    title: str = ""


@dataclass(frozen=True)
class HistoricalState:
    """Polled historical data (refreshed every ~60 seconds)."""

    # From stats-cache.json
    daily_activity: list[DailyActivity] = field(default_factory=list)
    daily_model_tokens: list[DailyModelTokens] = field(default_factory=list)

    # Recent Codex sessions from SQLite
    recent_codex_sessions: list[CodexSessionSummary] = field(default_factory=list)

    last_updated: datetime = field(default_factory=datetime.now)

    @property
    def today_activity(self) -> DailyActivity | None:
        today = datetime.now().strftime("%Y-%m-%d")
        for entry in reversed(self.daily_activity):
            if entry.date == today:
                return entry
        return None

    @property
    def today_tokens(self) -> DailyModelTokens | None:
        today = datetime.now().strftime("%Y-%m-%d")
        for entry in reversed(self.daily_model_tokens):
            if entry.date == today:
                return entry
        return None


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventLogEntry:
    """A single timestamped event log entry."""

    timestamp: datetime
    level: str       # INFO | WARNING | ERROR | SUCCESS | METRIC
    message: str
    source: str = ""  # "claude" | "codex" | "system"


# ---------------------------------------------------------------------------
# Top-level dashboard state (mutable container, updated under lock)
# ---------------------------------------------------------------------------


class DashboardState:
    """
    Shared mutable container for all dashboard state.

    All reads and writes must be done under `self.lock`.
    Collectors replace the frozen dataclass instances atomically.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.claude: ClaudeState = ClaudeState()
        self.codex: CodexState = CodexState()
        self.historical: HistoricalState = HistoricalState()
        self.events: list[EventLogEntry] = []

        # Tracks which thresholds have already fired to avoid duplicate alerts
        self._claude_warn_fired: bool = False
        self._claude_crit_fired: bool = False
        self._codex_warn_fired: bool = False
        self._codex_crit_fired: bool = False

    def update_claude(self, new_state: ClaudeState) -> None:
        with self.lock:
            self.claude = new_state

    def update_codex(self, new_state: CodexState) -> None:
        with self.lock:
            self.codex = new_state

    def update_historical(self, new_state: HistoricalState) -> None:
        with self.lock:
            self.historical = new_state

    def add_event(self, entry: EventLogEntry, max_entries: int = 100) -> None:
        with self.lock:
            self.events = (self.events + [entry])[-max_entries:]

    def snapshot(self) -> tuple[ClaudeState, CodexState, HistoricalState, list[EventLogEntry]]:
        """Return a consistent read of all state under the lock."""
        with self.lock:
            return self.claude, self.codex, self.historical, list(self.events)
