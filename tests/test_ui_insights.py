"""Tests for UI-only insight derivation."""

from __future__ import annotations

from datetime import datetime, timedelta

from state import ClaudeState, CodexState, HistoricalState
from ui.insights import DashboardUIState, SnapshotSample, derive_dashboard_insights


def _sample(
    *,
    seconds_ago: int,
    claude_fill: float,
    claude_output_total: int,
    codex_fill: float,
    codex_total_tokens: int,
) -> SnapshotSample:
    now = datetime.now() - timedelta(seconds=seconds_ago)
    return SnapshotSample(
        timestamp=now,
        claude_fill_pct=claude_fill,
        claude_context_tokens=int(claude_fill * 1000),
        claude_output_total=claude_output_total,
        codex_fill_pct=codex_fill,
        codex_context_tokens=int(codex_fill * 1000),
        codex_total_tokens=codex_total_tokens,
    )


def test_derives_headroom_and_status():
    ui_state = DashboardUIState()
    ui_state.samples.extend(
        [
            _sample(seconds_ago=20, claude_fill=20.0, claude_output_total=100, codex_fill=10.0, codex_total_tokens=500),
            _sample(seconds_ago=0, claude_fill=23.0, claude_output_total=140, codex_fill=14.0, codex_total_tokens=700),
        ]
    )
    claude = ClaudeState(
        session_id="claude",
        model="claude-sonnet-4-6",
        context_window=200_000,
        latest_input_tokens=40_000,
        latest_cache_read_tokens=6_000,
        last_updated=datetime.now(),
        output_tokens_total=140,
    )
    codex = CodexState(
        session_id="codex",
        model="gpt-5.4",
        context_window=258_400,
        total_tokens=700,
        current_context_tokens=36_000,
        last_updated=datetime.now(),
    )

    insights = derive_dashboard_insights(claude, codex, HistoricalState(), ui_state)

    assert insights.claude.headroom_tokens == 154_000
    assert insights.codex.headroom_tokens == 222_400
    assert insights.claude.status_label in {"healthy", "rising"}


def test_marks_stale_active_session():
    ui_state = DashboardUIState()
    stale_time = datetime.now() - timedelta(minutes=5)
    claude = ClaudeState(
        session_id="claude",
        model="claude-sonnet-4-6",
        context_window=200_000,
        latest_input_tokens=10_000,
        last_updated=stale_time,
    )
    codex = CodexState(
        session_id="codex",
        model="gpt-5.4",
        context_window=258_400,
        current_context_tokens=12_000,
        total_tokens=50_000,
        last_updated=stale_time,
    )

    insights = derive_dashboard_insights(claude, codex, HistoricalState(), ui_state)

    assert insights.claude.is_stale is True
    assert insights.codex.is_stale is True
    assert "History off" in insights.status_line


def test_derives_velocity_text():
    ui_state = DashboardUIState()
    ui_state.samples.extend(
        [
            _sample(seconds_ago=60, claude_fill=30.0, claude_output_total=100, codex_fill=15.0, codex_total_tokens=1_000),
            _sample(seconds_ago=0, claude_fill=34.0, claude_output_total=400, codex_fill=19.0, codex_total_tokens=7_000),
        ]
    )
    claude = ClaudeState(
        session_id="claude",
        model="claude-sonnet-4-6",
        context_window=200_000,
        latest_input_tokens=68_000,
        last_updated=datetime.now(),
        output_tokens_total=400,
    )
    codex = CodexState(
        session_id="codex",
        model="gpt-5.4",
        context_window=258_400,
        current_context_tokens=49_000,
        total_tokens=7_000,
        last_updated=datetime.now(),
    )

    insights = derive_dashboard_insights(claude, codex, HistoricalState(), ui_state)

    assert "Output +" in insights.claude.velocity_label
    assert "Session +" in insights.codex.velocity_label
