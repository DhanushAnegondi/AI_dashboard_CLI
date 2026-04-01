"""
Tests for collectors/stats_collector.py

Tests cover:
- JSON parsing of dailyActivity and dailyModelTokens
- Handles missing file gracefully
- Handles malformed JSON gracefully
- today_activity and today_tokens helpers on HistoricalState
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.stats_collector import _parse_daily_activity, _parse_daily_model_tokens, load_stats_cache
from state import DailyActivity, DailyModelTokens, HistoricalState


# ---------------------------------------------------------------------------
# _parse_daily_activity
# ---------------------------------------------------------------------------


class TestParseDailyActivity:
    def test_parses_valid_entries(self):
        raw = [
            {"date": "2026-03-16", "messageCount": 2157, "sessionCount": 8, "toolCallCount": 238},
            {"date": "2026-03-02", "messageCount": 14, "sessionCount": 2, "toolCallCount": 0},
        ]
        result = _parse_daily_activity(raw)
        assert len(result) == 2
        assert result[0].date == "2026-03-16"
        assert result[0].message_count == 2157
        assert result[0].session_count == 8
        assert result[0].tool_call_count == 238

    def test_missing_fields_default_to_zero(self):
        result = _parse_daily_activity([{"date": "2026-01-01"}])
        assert result[0].message_count == 0
        assert result[0].session_count == 0
        assert result[0].tool_call_count == 0

    def test_empty_list_returns_empty(self):
        assert _parse_daily_activity([]) == []


# ---------------------------------------------------------------------------
# _parse_daily_model_tokens
# ---------------------------------------------------------------------------


class TestParseDailyModelTokens:
    def test_parses_valid_entries(self):
        raw = [
            {"date": "2026-03-16", "tokensByModel": {"claude-sonnet-4-6": 66481, "claude-opus-4-6": 24}},
        ]
        result = _parse_daily_model_tokens(raw)
        assert len(result) == 1
        assert result[0].date == "2026-03-16"
        assert result[0].tokens_by_model["claude-sonnet-4-6"] == 66481
        assert result[0].total_tokens == 66505

    def test_missing_tokens_defaults_to_empty_dict(self):
        result = _parse_daily_model_tokens([{"date": "2026-01-01"}])
        assert result[0].tokens_by_model == {}
        assert result[0].total_tokens == 0

    def test_empty_list_returns_empty(self):
        assert _parse_daily_model_tokens([]) == []


# ---------------------------------------------------------------------------
# load_stats_cache
# ---------------------------------------------------------------------------


class TestLoadStatsCache:
    def _write_cache(self, tmp_path: Path, data: dict) -> Path:
        path = tmp_path / "stats-cache.json"
        path.write_text(json.dumps(data))
        return path

    def test_loads_valid_cache(self, tmp_path):
        data = {
            "dailyActivity": [
                {"date": "2026-03-16", "messageCount": 100, "sessionCount": 2, "toolCallCount": 10}
            ],
            "dailyModelTokens": [
                {"date": "2026-03-16", "tokensByModel": {"claude-sonnet-4-6": 5000}}
            ],
        }
        path = self._write_cache(tmp_path, data)
        result = load_stats_cache(path)
        assert result is not None
        activity, tokens = result
        assert len(activity) == 1
        assert len(tokens) == 1

    def test_returns_none_for_missing_file(self, tmp_path):
        assert load_stats_cache(tmp_path / "missing.json") is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        path = tmp_path / "stats-cache.json"
        path.write_text("not valid json {{{")
        assert load_stats_cache(path) is None

    def test_handles_empty_arrays(self, tmp_path):
        path = self._write_cache(tmp_path, {"dailyActivity": [], "dailyModelTokens": []})
        result = load_stats_cache(path)
        assert result == ([], [])


# ---------------------------------------------------------------------------
# HistoricalState helpers
# ---------------------------------------------------------------------------


class TestHistoricalStateHelpers:
    def _make_historical(self, dates: list[str]) -> HistoricalState:
        from state import DailyActivity, DailyModelTokens
        activity = [DailyActivity(date=d, message_count=10) for d in dates]
        tokens = [DailyModelTokens(date=d, tokens_by_model={"m": 100}) for d in dates]
        return HistoricalState(daily_activity=activity, daily_model_tokens=tokens)

    def test_today_activity_returns_todays_entry(self):
        today = datetime.now().strftime("%Y-%m-%d")
        hist = self._make_historical(["2026-01-01", today])
        assert hist.today_activity is not None
        assert hist.today_activity.date == today

    def test_today_activity_returns_none_when_no_entry(self):
        hist = self._make_historical(["2025-01-01"])
        assert hist.today_activity is None

    def test_today_tokens_returns_todays_entry(self):
        today = datetime.now().strftime("%Y-%m-%d")
        hist = self._make_historical([today])
        assert hist.today_tokens is not None
        assert hist.today_tokens.date == today
