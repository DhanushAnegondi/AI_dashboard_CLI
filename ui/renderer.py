"""Layout composition for the AI dashboard."""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from config import UI_REFRESH_SECONDS
from state import DashboardState
from ui.feature_versions import feature_version
from ui.insights import DashboardInsights, DashboardUIState, derive_dashboard_insights
from ui.panels import (
    make_claude_panel,
    make_codex_panel,
    make_events_panel,
    make_history_panel,
    make_today_panel,
)


def _header_panel(claude_active: bool, codex_active: bool, insights: DashboardInsights) -> Panel:
    claude_dot = "[green]●[/green]" if claude_active else "[dim]○[/dim]"
    codex_dot = "[green]●[/green]" if codex_active else "[dim]○[/dim]"
    header = Text.from_markup(
        "AI Usage Dashboard   "
        f"{claude_dot} Claude Code   "
        f"{codex_dot} Codex   "
        f"[dim][refresh: {UI_REFRESH_SECONDS:.0f}s][/dim]   "
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
    )
    body = Group(
        header,
        Text(insights.status_line, style="bold"),
        Text(insights.controls_line, style="dim"),
    )
    return Panel(
        body,
        title=f"Status ({feature_version('header_status')})",
        border_style="bright_blue",
    )


def build_layout(state: DashboardState, ui_state: DashboardUIState, *, width: int = 120) -> Layout:
    """Build the full live layout from the current state snapshot."""
    claude, codex, historical, events = state.snapshot()
    insights = derive_dashboard_insights(claude, codex, historical, ui_state)

    layout = Layout(name="root")
    sections = [
        Layout(name="header", size=5),
        Layout(name="body", ratio=1),
        Layout(name="today", size=8),
    ]
    if ui_state.show_history:
        sections.append(Layout(name="history", size=14))
    sections.append(Layout(name="events", size=12))
    layout.split_column(*sections)

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

    layout["header"].update(_header_panel(claude.is_active, codex.is_active, insights))
    layout["claude"].update(make_claude_panel(claude, insights.claude))
    layout["codex"].update(make_codex_panel(codex, insights.codex))
    layout["today"].update(make_today_panel(historical))
    if ui_state.show_history:
        layout["history"].update(make_history_panel(historical))
    layout["events"].update(make_events_panel(events))

    return layout
