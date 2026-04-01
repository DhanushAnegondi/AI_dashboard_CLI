"""
Tests for collectors/claude_collector.py

Tests cover:
- JSONL line parsing (usage extraction, model detection)
- Session file discovery
- Full-file parse accumulation
- Context window resolution
- State accumulation across multiple lines
"""

import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

# Make the parent directory importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.claude_collector import (
    _extract_model_from_line,
    _find_active_session_file,
    _full_parse_session,
    _parse_usage_from_line,
    _resolve_context_window,
    _session_id_from_path,
)
from state import ClaudeUsage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_assistant_line(
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 200,
    cache_create: int = 10,
) -> str:
    entry = {
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            },
        }
    }
    return json.dumps(entry)


def _make_user_line() -> str:
    return json.dumps({"message": {"role": "user", "content": "hello"}})


# ---------------------------------------------------------------------------
# _parse_usage_from_line
# ---------------------------------------------------------------------------


class TestParseUsageFromLine:
    def test_returns_usage_for_valid_assistant_line(self):
        raw = _make_assistant_line(input_tokens=42, output_tokens=7, cache_read=100, cache_create=5)
        usage = _parse_usage_from_line(raw)
        assert usage is not None
        assert usage.input_tokens == 42
        assert usage.output_tokens == 7
        assert usage.cache_read_input_tokens == 100
        assert usage.cache_creation_input_tokens == 5

    def test_returns_none_for_user_line(self):
        assert _parse_usage_from_line(_make_user_line()) is None

    def test_returns_none_for_invalid_json(self):
        assert _parse_usage_from_line("not json {{") is None

    def test_returns_none_for_empty_string(self):
        assert _parse_usage_from_line("") is None

    def test_returns_none_when_no_usage_block(self):
        line = json.dumps({"message": {"role": "assistant", "content": "hi"}})
        assert _parse_usage_from_line(line) is None

    def test_handles_missing_token_fields_gracefully(self):
        line = json.dumps({"message": {"role": "assistant", "usage": {}}})
        usage = _parse_usage_from_line(line)
        assert usage is not None
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_total_context_tokens_sums_correctly(self):
        raw = _make_assistant_line(input_tokens=10, cache_read=20, cache_create=5)
        usage = _parse_usage_from_line(raw)
        assert usage.total_context_tokens == 35  # 10 + 20 + 5


# ---------------------------------------------------------------------------
# _extract_model_from_line
# ---------------------------------------------------------------------------


class TestExtractModelFromLine:
    def test_extracts_model_from_assistant_line(self):
        raw = _make_assistant_line(model="claude-sonnet-4-6")
        assert _extract_model_from_line(raw) == "claude-sonnet-4-6"

    def test_returns_none_for_user_line(self):
        assert _extract_model_from_line(_make_user_line()) is None

    def test_returns_none_for_invalid_json(self):
        assert _extract_model_from_line("bad json") is None


# ---------------------------------------------------------------------------
# _resolve_context_window
# ---------------------------------------------------------------------------


class TestResolveContextWindow:
    def test_known_model_returns_correct_size(self):
        assert _resolve_context_window("claude-sonnet-4-6") == 200_000

    def test_unknown_model_returns_default(self):
        assert _resolve_context_window("gpt-99") == 200_000

    def test_empty_model_returns_default(self):
        assert _resolve_context_window("") == 200_000

    def test_prefix_match_works(self):
        # A versioned model that starts with a known prefix
        assert _resolve_context_window("claude-opus-4-6-20260101") == 200_000


# ---------------------------------------------------------------------------
# _session_id_from_path
# ---------------------------------------------------------------------------


class TestSessionIdFromPath:
    def test_returns_first_8_chars_of_stem(self):
        path = Path("/tmp/5a72839e-b69f-43f9-8d32-55901b8544d4.jsonl")
        assert _session_id_from_path(path) == "5a72839e"

    def test_short_stem_returns_full(self):
        path = Path("/tmp/abc.jsonl")
        assert _session_id_from_path(path) == "abc"


# ---------------------------------------------------------------------------
# _find_active_session_file
# ---------------------------------------------------------------------------


class TestFindActiveSessionFile:
    def test_returns_none_for_empty_dir(self, tmp_path):
        assert _find_active_session_file(tmp_path) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        result = _find_active_session_file(tmp_path / "nonexistent")
        assert result is None

    def test_finds_recently_modified_file(self, tmp_path):
        session_file = tmp_path / "abc123.jsonl"
        session_file.write_text(_make_assistant_line())
        result = _find_active_session_file(tmp_path)
        assert result == session_file

    def test_ignores_old_files(self, tmp_path, monkeypatch):
        import time

        old_file = tmp_path / "old.jsonl"
        old_file.write_text(_make_assistant_line())

        # Patch time.time to simulate that file is very old
        original_stat = old_file.stat

        import os

        def patched_time():
            return original_stat().st_mtime + 7200 + 1  # 2h + 1s in the future

        monkeypatch.setattr("collectors.claude_collector.time.time", patched_time)
        assert _find_active_session_file(tmp_path) is None


# ---------------------------------------------------------------------------
# _full_parse_session
# ---------------------------------------------------------------------------


class TestFullParseSession:
    def test_last_usage_reflects_final_turn(self, tmp_path):
        session = tmp_path / "session.jsonl"
        lines = [
            _make_assistant_line(model="claude-sonnet-4-6", input_tokens=10, output_tokens=5, cache_read=0, cache_create=0),
            _make_user_line(),
            _make_assistant_line(model="claude-sonnet-4-6", input_tokens=20, output_tokens=8, cache_read=100, cache_create=50),
        ]
        session.write_text("\n".join(lines) + "\n")

        last, cumulative, model = _full_parse_session(session)
        # last should be the final turn only
        assert model == "claude-sonnet-4-6"
        assert last.input_tokens == 20
        assert last.output_tokens == 8
        assert last.cache_read_input_tokens == 100
        assert last.cache_creation_input_tokens == 50

    def test_cumulative_sums_all_output_tokens(self, tmp_path):
        session = tmp_path / "session.jsonl"
        lines = [
            _make_assistant_line(output_tokens=5),
            _make_assistant_line(output_tokens=8),
        ]
        session.write_text("\n".join(lines) + "\n")
        _, cumulative, _ = _full_parse_session(session)
        assert cumulative.output_tokens == 13

    def test_returns_zero_usage_for_empty_file(self, tmp_path):
        session = tmp_path / "empty.jsonl"
        session.write_text("")
        last, _, model = _full_parse_session(session)
        assert last.input_tokens == 0
        assert model == ""

    def test_handles_nonexistent_file(self, tmp_path):
        last, _, model = _full_parse_session(tmp_path / "missing.jsonl")
        assert last.input_tokens == 0
        assert model == ""

    def test_skips_invalid_json_lines(self, tmp_path):
        session = tmp_path / "session.jsonl"
        session.write_text(
            "not json\n"
            + _make_assistant_line(input_tokens=5)
            + "\n"
            + "{broken\n"
        )
        last, _, _ = _full_parse_session(session)
        assert last.input_tokens == 5


# ---------------------------------------------------------------------------
# ClaudeUsage properties
# ---------------------------------------------------------------------------


class TestClaudeUsageProperties:
    def test_total_context_tokens(self):
        u = ClaudeUsage(
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=300,
        )
        assert u.total_context_tokens == 3300  # input + cache_read + cache_create
