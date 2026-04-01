"""
stats_collector.py — Polls ~/.claude/stats-cache.json every STATS_POLL_SECONDS.

Parses dailyActivity and dailyModelTokens arrays and pushes them into
DashboardState.historical. Does not use watchdog — the file is written
infrequently and polling every 60 seconds is sufficient.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from config import CLAUDE_STATS_CACHE, MAX_EVENT_LOG_ENTRIES, STATS_POLL_SECONDS
from state import (
    DailyActivity,
    DailyModelTokens,
    DashboardState,
    EventLogEntry,
    HistoricalState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_daily_activity(raw: list[dict]) -> list[DailyActivity]:
    result = []
    for entry in raw:
        result.append(
            DailyActivity(
                date=entry.get("date", ""),
                message_count=entry.get("messageCount", 0),
                session_count=entry.get("sessionCount", 0),
                tool_call_count=entry.get("toolCallCount", 0),
            )
        )
    return result


def _parse_daily_model_tokens(raw: list[dict]) -> list[DailyModelTokens]:
    result = []
    for entry in raw:
        result.append(
            DailyModelTokens(
                date=entry.get("date", ""),
                tokens_by_model=entry.get("tokensByModel", {}),
            )
        )
    return result


def load_stats_cache(path: Path) -> tuple[list[DailyActivity], list[DailyModelTokens]] | None:
    """
    Read and parse stats-cache.json.
    Returns (daily_activity, daily_model_tokens) or None on failure.
    """
    if not path.exists():
        logger.debug("stats-cache.json not found at %s", path)
        return None

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read stats-cache.json: %s", exc)
        return None

    activity = _parse_daily_activity(data.get("dailyActivity", []))
    tokens = _parse_daily_model_tokens(data.get("dailyModelTokens", []))
    return activity, tokens


# ---------------------------------------------------------------------------
# Poll worker
# ---------------------------------------------------------------------------


class StatsCollector(threading.Thread):
    """
    Background thread that polls stats-cache.json every STATS_POLL_SECONDS
    and updates DashboardState.historical with Claude daily stats.

    Lifecycle:
        collector = StatsCollector(dashboard_state)
        collector.start()
        ...
        collector.stop()
    """

    def __init__(self, state: DashboardState) -> None:
        super().__init__(daemon=True, name="StatsCollector")
        self._state = state
        self._stop_event = threading.Event()

    def run(self) -> None:
        # Poll immediately on start, then at regular intervals
        self._poll()
        while not self._stop_event.wait(timeout=STATS_POLL_SECONDS):
            self._poll()

    def stop(self) -> None:
        self._stop_event.set()

    def _poll(self) -> None:
        result = load_stats_cache(CLAUDE_STATS_CACHE)
        if result is None:
            return

        activity, tokens = result

        with self._state.lock:
            old = self._state.historical
            new_hist = HistoricalState(
                daily_activity=activity,
                daily_model_tokens=tokens,
                recent_codex_sessions=old.recent_codex_sessions,
                last_updated=datetime.now(),
            )
            self._state.historical = new_hist

        self._state.add_event(
            EventLogEntry(
                timestamp=datetime.now(),
                level="INFO",
                message="stats-cache.json refreshed",
                source="system",
            ),
            max_entries=MAX_EVENT_LOG_ENTRIES,
        )
        logger.debug("Stats cache refreshed: %d activity days, %d token days", len(activity), len(tokens))
