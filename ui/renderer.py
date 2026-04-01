"""Layout composition for the AI dashboard."""

from __future__ import annotations

from datetime import datetime

from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from config import UI_REFRESH_SECONDS
from state import DashboardState
from ui.panels import (
    make_claude_panel,
    make_codex_panel,
    make_events_panel,
    make_today_panel,
)


def _header_panel(claude_active: bool, codex_active: bool) -> Panel:
    claude_dot = "[green]●[/green]" if claude_active else "[dim]○[/dim]"
    codex_dot = "[green]●[/green]" if codex_active else "[dim]○[/dim]"
    text = Text.from_markup(
        "AI Usage Dashboard   "
        f"{claude_dot} Claude Code   "
        f"{codex_dot} Codex   "
        f"[dim][refresh: {UI_REFRESH_SECONDS:.0f}s][/dim]   "
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
    )
    return Panel(text, border_style="bright_blue")


def build_layout(state: DashboardState, *, width: int = 120) -> Layout:
    """Build the full live layout from the current state snapshot."""
    claude, codex, historical, events = state.snapshot()

    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="today", size=8),
        Layout(name="events", size=12),
    )

    if width < 100:
        layout["body"].split_column(
            Layout(name="claude"),
            Layout(name="codex"),
        )
    else:
        layout["body"].split_row(
            Layout(name="claude"),
            Layout(name="codex"),
        )

    layout["header"].update(_header_panel(claude.is_active, codex.is_active))
    layout["claude"].update(make_claude_panel(claude))
    layout["codex"].update(make_codex_panel(codex))
    layout["today"].update(make_today_panel(historical))
    layout["events"].update(make_events_panel(events))

    return layout
