"""
config.py — All constants, paths, and style definitions for the AI dashboard.

Single source of truth. Nothing here is computed at runtime except path resolution.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS_DIR: Path = Path.home() / ".claude" / "projects"
CLAUDE_STATS_CACHE: Path = Path.home() / ".claude" / "stats-cache.json"

CODEX_SESSIONS_DIR: Path = Path.home() / ".codex" / "sessions"
CODEX_SQLITE_DB: Path = Path.home() / ".codex" / "state_5.sqlite"

# ---------------------------------------------------------------------------
# Context window sizes (tokens) — keyed by model name prefix
# ---------------------------------------------------------------------------

CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
}

DEFAULT_CLAUDE_CONTEXT_WINDOW: int = 200_000

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

WARN_THRESHOLD_PCT: float = 70.0   # yellow warning
CRIT_THRESHOLD_PCT: float = 90.0   # red critical

# How recently a session file must have been modified to be considered active
ACTIVE_SESSION_WINDOW_SECONDS: int = 3600  # 1 hour

# ---------------------------------------------------------------------------
# Refresh / polling intervals
# ---------------------------------------------------------------------------

UI_REFRESH_SECONDS: float = 2.0
STATS_POLL_SECONDS: int = 60
CODEX_DB_POLL_SECONDS: int = 5

# ---------------------------------------------------------------------------
# Event log: maximum entries kept in memory
# ---------------------------------------------------------------------------

MAX_EVENT_LOG_ENTRIES: int = 100

# ---------------------------------------------------------------------------
# Event level styles — (symbol, rich color)
# ---------------------------------------------------------------------------

LEVEL_STYLES: dict[str, tuple[str, str]] = {
    "INFO":    ("●", "bright_blue"),
    "WARNING": ("⚠", "yellow"),
    "ERROR":   ("✗", "bold red"),
    "SUCCESS": ("✓", "green"),
    "METRIC":  ("◆", "cyan"),
}

# ---------------------------------------------------------------------------
# Context bar color thresholds
# ---------------------------------------------------------------------------

def context_bar_color(pct: float) -> str:
    """Return a rich color string based on context fill percentage."""
    if pct >= CRIT_THRESHOLD_PCT:
        return "bold red"
    if pct >= WARN_THRESHOLD_PCT:
        return "yellow"
    if pct >= 50.0:
        return "dark_orange"
    return "green"
