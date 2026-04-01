"""Entry point for the AI usage dashboard TUI."""

from __future__ import annotations

import argparse
import logging
import select
import sys
import termios
import time
import tty
from datetime import datetime, timedelta

from rich.console import Console
from rich.live import Live

from alerts import check_thresholds
from collectors.claude_collector import ClaudeCollector
from collectors.codex_collector import CodexCollector
from collectors.stats_collector import StatsCollector
from state import (
    ClaudeState,
    CodexRateLimits,
    CodexSessionSummary,
    CodexState,
    DailyActivity,
    DailyModelTokens,
    DashboardState,
    EventLogEntry,
    HistoricalState,
)
from ui.insights import DashboardUIState
from ui.renderer import build_layout


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _seed_mock_data(state: DashboardState) -> None:
    """Populate the dashboard with mock data for layout validation."""
    now = datetime.now()
    state.update_claude(
        ClaudeState(
            session_id="5a72839e",
            model="claude-sonnet-4-6",
            context_window=200_000,
            latest_input_tokens=1,
            latest_cache_read_tokens=92_939,
            latest_cache_creation_tokens=785,
            latest_output_tokens=521,
            output_tokens_total=42_145,
            cache_creation_total=239_464,
            session_file="mock://claude",
            last_updated=now,
        )
    )
    state.update_codex(
        CodexState(
            session_id="019d40dd",
            model="gpt-5.4",
            context_window=258_400,
            input_tokens=8_781,
            cached_input_tokens=7_680,
            output_tokens=40,
            reasoning_output_tokens=24,
            total_tokens=17_630,
            current_context_tokens=17_630,
            rate_limits=CodexRateLimits(
                primary_used_pct=1.0,
                primary_window_minutes=300,
                primary_resets_at=int((now + timedelta(hours=4, minutes=52)).timestamp()),
                secondary_used_pct=0.0,
                secondary_window_minutes=10_080,
                secondary_resets_at=int((now + timedelta(days=6, hours=23)).timestamp()),
            ),
            session_file="mock://codex",
            last_updated=now,
        )
    )
    state.update_historical(
        HistoricalState(
            daily_activity=[
                DailyActivity(
                    date=(now - timedelta(days=day)).strftime("%Y-%m-%d"),
                    message_count=count,
                    session_count=max(1, count // 120),
                    tool_call_count=count // 3,
                )
                for day, count in reversed(
                    list(
                        enumerate(
                            [
                                377,
                                233,
                                144,
                                89,
                                55,
                                34,
                                21,
                                13,
                                8,
                                5,
                                3,
                                4,
                                5,
                                6,
                                8,
                                11,
                                17,
                                28,
                                49,
                                112,
                                73,
                                52,
                                24,
                                18,
                            ]
                        )
                    )
                )
            ],
            daily_model_tokens=[
                DailyModelTokens(
                    date=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
                    tokens_by_model={
                        "claude-sonnet-4-6": 52_105,
                        "gpt-5.4": 11_870,
                    },
                ),
                DailyModelTokens(
                    date=now.strftime("%Y-%m-%d"),
                    tokens_by_model={
                        "claude-sonnet-4-6": 66_505,
                        "gpt-5.4": 17_630,
                    },
                )
            ],
            recent_codex_sessions=[
                CodexSessionSummary(
                    session_id="019d40dd",
                    model="gpt-5.4",
                    tokens_used=17_630,
                    created_at=int(now.timestamp()),
                    cwd="/Users/dhanushchandra/Downloads/Vibe_Coding_Projects",
                    title="AI dashboard TUI",
                ),
                CodexSessionSummary(
                    session_id="017ab111",
                    model="gpt-5.4-mini",
                    tokens_used=8_422,
                    created_at=int((now - timedelta(hours=6)).timestamp()),
                    cwd="/tmp/demo",
                    title="Prompt cleanup",
                ),
            ],
            last_updated=now,
        )
    )
    for level, message, source in [
        ("INFO", "Mock dashboard started", "system"),
        ("INFO", "Claude session active: 5a72839e", "claude"),
        ("INFO", "Codex session active: 019d40dd", "codex"),
    ]:
        state.add_event(EventLogEntry(timestamp=now, level=level, message=message, source=source))


class _KeyboardListener:
    """Small stdin poller so `q` can quit the live dashboard."""

    def __init__(self) -> None:
        self._enabled = sys.stdin.isatty()
        self._old_settings: list[int] | None = None

    def __enter__(self) -> "_KeyboardListener":
        if self._enabled:
            self._old_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._enabled and self._old_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)

    def read_key(self) -> str | None:
        if not self._enabled:
            return None
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if not readable:
            return None
        return sys.stdin.read(1).lower()


def _start_collectors(state: DashboardState) -> list[object]:
    collectors: list[object] = [
        ClaudeCollector(state),
        CodexCollector(state),
        StatsCollector(state),
    ]
    for collector in collectors:
        collector.start()
    return collectors


def _stop_collectors(collectors: list[object]) -> None:
    for collector in reversed(collectors):
        stop = getattr(collector, "stop", None)
        if callable(stop):
            stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI usage dashboard TUI")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Render with mock data instead of starting collectors",
    )
    args = parser.parse_args()

    _configure_logging()
    console = Console()
    state = DashboardState()
    ui_state = DashboardUIState()
    collectors: list[object] = []

    if args.mock:
        _seed_mock_data(state)
    else:
        collectors = _start_collectors(state)
    ui_state.record_snapshot(state)

    try:
        with _KeyboardListener() as keyboard:
            with Live(
                build_layout(state, ui_state, width=console.size.width),
                console=console,
                refresh_per_second=0.5,
                screen=True,
            ) as live:
                while True:
                    key = keyboard.read_key()
                    if key == "q":
                        break
                    if key == "h":
                        ui_state.toggle_history()
                    check_thresholds(state)
                    ui_state.record_snapshot(state)
                    live.update(build_layout(state, ui_state, width=console.size.width))
                    time.sleep(2.0)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_collectors(collectors)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
