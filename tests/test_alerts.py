"""
Tests for alerts.py

Tests cover:
- No events emitted below warn threshold
- Warning event emitted when Claude/Codex context crosses 70%
- Critical event emitted at 90% (not duplicate warn)
- Fired flags prevent duplicate events on subsequent ticks
- Flags reset on recovery (fill drops below threshold)
- Sound is NOT called in tests (patched out)
"""

import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from alerts import check_thresholds
from state import ClaudeState, CodexState, DashboardState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_claude(fill_pct: float, context_window: int = 200_000) -> ClaudeState:
    """Build a ClaudeState with a specific context fill percentage."""
    tokens = int(fill_pct / 100.0 * context_window)
    return ClaudeState(
        session_id="test",
        model="claude-sonnet-4-6",
        context_window=context_window,
        latest_input_tokens=tokens,
        last_updated=datetime.now(),
    )


def _make_codex(fill_pct: float, context_window: int = 258_400) -> CodexState:
    """Build a CodexState with a specific context fill percentage."""
    tokens = int(fill_pct / 100.0 * context_window)
    return CodexState(
        session_id="test",
        model="gpt-5.4",
        context_window=context_window,
        total_tokens=tokens,
        last_updated=datetime.now(),
    )


def _event_levels(state: DashboardState) -> list[str]:
    return [e.level for e in state.events]


# ---------------------------------------------------------------------------
# Below threshold — no events
# ---------------------------------------------------------------------------


class TestBelowThreshold:
    @patch("alerts._play_sound")
    def test_no_events_when_claude_below_70(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=65.0))
        check_thresholds(state)
        assert state.events == []
        mock_sound.assert_not_called()

    @patch("alerts._play_sound")
    def test_no_events_when_codex_below_70(self, mock_sound):
        state = DashboardState()
        state.update_codex(_make_codex(fill_pct=50.0))
        check_thresholds(state)
        assert state.events == []
        mock_sound.assert_not_called()


# ---------------------------------------------------------------------------
# Warning threshold (70%)
# ---------------------------------------------------------------------------


class TestWarnThreshold:
    @patch("alerts._play_sound")
    def test_warning_emitted_for_claude_at_70(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=72.0))
        check_thresholds(state)
        assert "WARNING" in _event_levels(state)
        mock_sound.assert_not_called()

    @patch("alerts._play_sound")
    def test_warning_emitted_for_codex_at_70(self, mock_sound):
        state = DashboardState()
        state.update_codex(_make_codex(fill_pct=75.0))
        check_thresholds(state)
        assert "WARNING" in _event_levels(state)
        mock_sound.assert_not_called()

    @patch("alerts._play_sound")
    def test_warning_not_duplicated_on_second_tick(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=72.0))
        check_thresholds(state)
        check_thresholds(state)
        warnings = [e for e in state.events if e.level == "WARNING" and e.source == "claude"]
        assert len(warnings) == 1  # only one, not two


# ---------------------------------------------------------------------------
# Critical threshold (90%)
# ---------------------------------------------------------------------------


class TestCritThreshold:
    @patch("alerts._play_sound")
    def test_error_emitted_for_claude_at_90(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=92.0))
        check_thresholds(state)
        assert "ERROR" in _event_levels(state)
        mock_sound.assert_called_once()

    @patch("alerts._play_sound")
    def test_error_emitted_for_codex_at_90(self, mock_sound):
        state = DashboardState()
        state.update_codex(_make_codex(fill_pct=91.0))
        check_thresholds(state)
        assert "ERROR" in _event_levels(state)
        mock_sound.assert_called_once()

    @patch("alerts._play_sound")
    def test_critical_does_not_also_emit_separate_warning(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=95.0))
        check_thresholds(state)
        # Should have ERROR, but NOT a separate WARNING for the same source
        claude_events = [e for e in state.events if e.source == "claude"]
        levels = [e.level for e in claude_events]
        assert "ERROR" in levels
        assert levels.count("WARNING") == 0

    @patch("alerts._play_sound")
    def test_critical_not_duplicated_on_second_tick(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=95.0))
        check_thresholds(state)
        check_thresholds(state)
        errors = [e for e in state.events if e.level == "ERROR" and e.source == "claude"]
        assert len(errors) == 1
        assert mock_sound.call_count == 1


# ---------------------------------------------------------------------------
# Recovery — flags reset
# ---------------------------------------------------------------------------


class TestRecovery:
    @patch("alerts._play_sound")
    def test_warn_re_emitted_after_recovery(self, mock_sound):
        state = DashboardState()

        # First crossing
        state.update_claude(_make_claude(fill_pct=72.0))
        check_thresholds(state)
        assert len(state.events) == 1

        # Recovery — drop below 70
        state.update_claude(_make_claude(fill_pct=60.0))
        check_thresholds(state)

        # Cross again — should emit a new warning
        state.update_claude(_make_claude(fill_pct=75.0))
        check_thresholds(state)

        warnings = [e for e in state.events if e.level == "WARNING" and e.source == "claude"]
        assert len(warnings) == 2  # one from each crossing


# ---------------------------------------------------------------------------
# Message content
# ---------------------------------------------------------------------------


class TestEventMessageContent:
    @patch("alerts._play_sound")
    def test_warning_message_contains_compact_hint(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=72.0))
        check_thresholds(state)
        claude_events = [e for e in state.events if e.source == "claude"]
        assert any("/compact" in e.message for e in claude_events)

    @patch("alerts._play_sound")
    def test_error_message_contains_critical_keyword(self, mock_sound):
        state = DashboardState()
        state.update_claude(_make_claude(fill_pct=92.0))
        check_thresholds(state)
        errors = [e for e in state.events if e.level == "ERROR" and e.source == "claude"]
        assert any("CRITICAL" in e.message.upper() for e in errors)
