"""
Tests for collectors/codex_collector.py

Tests cover:
- token_count event parsing from rollout JSONL
- session ID extraction from rollout filenames
- active rollout file discovery
- full rollout file parsing (last token_count wins)
- SQLite session query (with a real in-memory database)
"""

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.codex_collector import (
    _find_active_rollout,
    _full_parse_rollout,
    _parse_token_count_event,
    _query_sqlite_sessions,
    _session_id_from_rollout,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_token_count_line(
    total_tokens: int = 1000,
    last_total_tokens: Optional[int] = None,
    input_tokens: int = 800,
    cached_input: int = 600,
    output_tokens: int = 100,
    reasoning_tokens: int = 0,
    context_window: int = 258_400,
    primary_pct: float = 1.0,
    primary_resets_at: int = 0,
    secondary_pct: float = 0.0,
    secondary_resets_at: int = 0,
) -> str:
    entry = {
        "timestamp": "2026-03-30T22:29:27.501Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": total_tokens,
                },
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": last_total_tokens if last_total_tokens is not None else total_tokens,
                },
                "model_context_window": context_window,
            },
            "rate_limits": {
                "primary": {
                    "used_percent": primary_pct,
                    "window_minutes": 300,
                    "resets_at": primary_resets_at,
                },
                "secondary": {
                    "used_percent": secondary_pct,
                    "window_minutes": 10080,
                    "resets_at": secondary_resets_at,
                },
            },
        },
    }
    return json.dumps(entry)


def _make_agent_message_line() -> str:
    return json.dumps({
        "type": "event_msg",
        "payload": {"type": "agent_message", "message": "Session ended.", "phase": "final_answer"},
    })


# ---------------------------------------------------------------------------
# _parse_token_count_event
# ---------------------------------------------------------------------------


class TestParseTokenCountEvent:
    def test_parses_valid_token_count_event(self):
        raw = _make_token_count_line(
            total_tokens=8821,
            input_tokens=8781,
            cached_input=7680,
            output_tokens=40,
            reasoning_tokens=24,
            context_window=258_400,
            primary_pct=1.0,
            secondary_pct=0.0,
        )
        partial, rate_limits = _parse_token_count_event(raw)

        assert partial is not None
        assert partial.total_tokens == 8821
        assert partial.input_tokens == 8781
        assert partial.cached_input_tokens == 7680
        assert partial.output_tokens == 40
        assert partial.reasoning_output_tokens == 24
        assert partial.context_window == 258_400
        assert partial.current_context_tokens == 8821

        assert rate_limits is not None
        assert rate_limits.primary_used_pct == 1.0
        assert rate_limits.secondary_used_pct == 0.0

    def test_returns_none_for_non_token_count_event(self):
        partial, rl = _parse_token_count_event(_make_agent_message_line())
        assert partial is None
        assert rl is None

    def test_returns_none_for_invalid_json(self):
        partial, rl = _parse_token_count_event("not json {")
        assert partial is None
        assert rl is None

    def test_returns_none_for_empty_string(self):
        partial, rl = _parse_token_count_event("")
        assert partial is None
        assert rl is None

    def test_context_fill_pct_calculated_correctly(self):
        raw = _make_token_count_line(total_tokens=999999, last_total_tokens=25840, context_window=258_400)
        partial, _ = _parse_token_count_event(raw)
        assert partial is not None
        assert abs(partial.context_fill_pct - 10.0) < 0.01

    def test_uses_last_token_usage_for_context_fill_when_present(self):
        raw = _make_token_count_line(total_tokens=500000, last_total_tokens=51680, context_window=258_400)
        partial, _ = _parse_token_count_event(raw)
        assert partial is not None
        assert partial.total_tokens == 500000
        assert partial.current_context_tokens == 51680
        assert abs(partial.context_fill_pct - 20.0) < 0.01


# ---------------------------------------------------------------------------
# _session_id_from_rollout
# ---------------------------------------------------------------------------


class TestSessionIdFromRollout:
    def test_extracts_uuid_prefix_from_standard_filename(self):
        path = Path("rollout-2026-03-30T15-28-45-019d40dd-1bd2-73d1-996d-1354dfb16259.jsonl")
        result = _session_id_from_rollout(path)
        # Should be the first 8 chars of the UUID portion
        assert len(result) == 8
        assert result == "019d40dd"

    def test_short_stem_returns_whatever_is_there(self):
        path = Path("rollout-abc.jsonl")
        result = _session_id_from_rollout(path)
        assert len(result) <= 8


# ---------------------------------------------------------------------------
# _find_active_rollout
# ---------------------------------------------------------------------------


class TestFindActiveRollout:
    def test_returns_none_for_empty_dir(self, tmp_path):
        assert _find_active_rollout(tmp_path) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        assert _find_active_rollout(tmp_path / "missing") is None

    def test_finds_recently_modified_rollout(self, tmp_path):
        rollout = tmp_path / "rollout-2026-03-30T12-00-00-abc123.jsonl"
        rollout.write_text(_make_token_count_line())
        result = _find_active_rollout(tmp_path)
        assert result == rollout

    def test_returns_most_recently_modified_file(self, tmp_path):
        old = tmp_path / "rollout-2026-03-30T10-00-00-aaa.jsonl"
        new = tmp_path / "rollout-2026-03-30T11-00-00-bbb.jsonl"
        old.write_text(_make_token_count_line())
        new.write_text(_make_token_count_line())
        # Touch new file to ensure it's newer
        import os
        os.utime(new, None)
        result = _find_active_rollout(tmp_path)
        assert result == new

    def test_ignores_non_rollout_files(self, tmp_path):
        not_rollout = tmp_path / "session.jsonl"
        not_rollout.write_text("{}")
        assert _find_active_rollout(tmp_path) is None


# ---------------------------------------------------------------------------
# _full_parse_rollout
# ---------------------------------------------------------------------------


class TestFullParseRollout:
    def test_returns_last_token_count_state(self, tmp_path):
        rollout = tmp_path / "rollout-test.jsonl"
        lines = [
            _make_token_count_line(total_tokens=100),
            _make_agent_message_line(),
            _make_token_count_line(total_tokens=500),  # this should win
        ]
        rollout.write_text("\n".join(lines) + "\n")

        result = _full_parse_rollout(rollout)
        assert result is not None
        assert result.total_tokens == 500

    def test_returns_none_for_file_with_no_token_events(self, tmp_path):
        rollout = tmp_path / "rollout-test.jsonl"
        rollout.write_text(_make_agent_message_line() + "\n")
        assert _full_parse_rollout(rollout) is None

    def test_returns_none_for_nonexistent_file(self, tmp_path):
        assert _full_parse_rollout(tmp_path / "missing.jsonl") is None

    def test_handles_mixed_valid_and_invalid_lines(self, tmp_path):
        rollout = tmp_path / "rollout-test.jsonl"
        lines = [
            "bad json",
            _make_token_count_line(total_tokens=42),
            "{broken",
        ]
        rollout.write_text("\n".join(lines))
        result = _full_parse_rollout(rollout)
        assert result is not None
        assert result.total_tokens == 42


# ---------------------------------------------------------------------------
# _query_sqlite_sessions
# ---------------------------------------------------------------------------


class TestQuerySqliteSessions:
    def _create_test_db(self, tmp_path: Path) -> Path:
        """Create a minimal in-memory-style SQLite DB mirroring Codex schema."""
        db_path = tmp_path / "state_5.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                model TEXT,
                tokens_used INTEGER,
                created_at INTEGER,
                cwd TEXT,
                title TEXT
            )
        """)
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
            ("session-1", "gpt-5.4", 17630, 1774909725, "/some/dir", "First session"),
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
            ("session-2", "gpt-5.4", 24262, 1774907052, "/other/dir", "Second session"),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_returns_sessions_ordered_by_created_at_desc(self, tmp_path):
        db = self._create_test_db(tmp_path)
        sessions = _query_sqlite_sessions(db, limit=10)
        assert len(sessions) == 2
        assert sessions[0].session_id == "session-1"
        assert sessions[0].tokens_used == 17630

    def test_respects_limit(self, tmp_path):
        db = self._create_test_db(tmp_path)
        sessions = _query_sqlite_sessions(db, limit=1)
        assert len(sessions) == 1

    def test_returns_empty_for_nonexistent_db(self, tmp_path):
        assert _query_sqlite_sessions(tmp_path / "missing.sqlite") == []

    def test_maps_fields_correctly(self, tmp_path):
        db = self._create_test_db(tmp_path)
        sessions = _query_sqlite_sessions(db)
        s = sessions[0]
        assert s.model == "gpt-5.4"
        assert s.cwd == "/some/dir"
        assert s.title == "First session"
        assert s.created_at == 1774909725
