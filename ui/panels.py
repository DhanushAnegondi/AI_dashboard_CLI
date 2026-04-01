"""Panel builders for the AI dashboard."""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import HISTORY_PANEL_ROWS
from config import CRIT_THRESHOLD_PCT, LEVEL_STYLES, WARN_THRESHOLD_PCT
from state import ClaudeState, CodexState, EventLogEntry, HistoricalState
from ui.feature_versions import feature_version
from ui.insights import SessionInsights
from ui.widgets import context_bar, format_tokens, sparkline


def _panel_border_style(fill_pct: float) -> str:
    if fill_pct >= CRIT_THRESHOLD_PCT:
        return "bold red"
    if fill_pct >= WARN_THRESHOLD_PCT:
        return "yellow"
    return "white"


def _format_reset_countdown(resets_at: int) -> str:
    if resets_at <= 0:
        return "Unknown"

    delta = max(0, resets_at - int(datetime.now().timestamp()))
    hours, rem = divmod(delta, 3600)
    mins = rem // 60
    return f"Resets in {hours}h {mins}m"


def make_claude_panel(state: ClaudeState, insights: SessionInsights) -> Panel:
    """Build the Claude status panel."""
    if not state.is_active:
        body = Text("No active session", style="dim")
        return Panel(body, title="Claude Code", border_style="white")

    body = Group(
        Text(f"Session  {state.session_id}", style="bold"),
        Text(f"Model    {state.model or 'Unknown'}", style="cyan"),
        Text(""),
        Text("Context Window", style="bold"),
        context_bar(state.total_context_tokens, state.context_window, state.context_fill_pct),
        Text(
            f"Headroom  {format_tokens(insights.headroom_tokens)} tokens remaining",
            style="green",
        ),
        Text(
            f"Last update  {insights.age_label}  |  Trend {insights.trend_symbol} {insights.trend_label}",
            style="dim",
        ),
        Text(
            f"Velocity  {insights.velocity_label}",
            style="cyan",
        ),
        Text(
            insights.stale_label,
            style="yellow" if insights.is_stale else "dim",
        ),
        Text(""),
        Text(
            "Latest turn  "
            f"in {format_tokens(state.latest_input_tokens)}  "
            f"cache-read {format_tokens(state.latest_cache_read_tokens)}  "
            f"cache-new {format_tokens(state.latest_cache_creation_tokens)}  "
            f"out {format_tokens(state.latest_output_tokens)}",
            style="dim",
        ),
        Text(
            "Session total  "
            f"out {format_tokens(state.output_tokens_total)}  "
            f"cache-created {format_tokens(state.cache_creation_total)}",
            style="green",
        ),
    )
    return Panel(
        body,
        title=f"Claude Code ({feature_version('headroom')})",
        border_style=_panel_border_style(state.context_fill_pct),
    )


def make_codex_panel(state: CodexState, insights: SessionInsights) -> Panel:
    """Build the Codex status panel."""
    if not state.is_active:
        body = Text("No active session", style="dim")
        return Panel(body, title="Codex", border_style="white")

    usage_hint = (
        "Current request at or above model context window"
        if state.current_context_tokens >= state.context_window
        else "Current request within model context window"
    )

    body = Group(
        Text(f"Session  {state.session_id}", style="bold"),
        Text(f"Model    {state.model or 'Unknown'}", style="cyan"),
        Text(""),
        Text("Context Window", style="bold"),
        context_bar(state.current_context_tokens, state.context_window, state.context_fill_pct),
        Text(
            f"Headroom  {format_tokens(insights.headroom_tokens)} tokens remaining",
            style="green",
        ),
        Text(
            usage_hint,
            style="bold red" if state.current_context_tokens >= state.context_window else "dim",
        ),
        Text(
            f"Last update  {insights.age_label}  |  Trend {insights.trend_symbol} {insights.trend_label}",
            style="dim",
        ),
        Text(
            f"Velocity  {insights.velocity_label}",
            style="cyan",
        ),
        Text(
            insights.stale_label,
            style="yellow" if insights.is_stale else "dim",
        ),
        Text(""),
        Text(
            "Current request  "
            f"{format_tokens(state.current_context_tokens)} tokens",
            style="green",
        ),
        Text(
            "Session total  "
            f"in {format_tokens(state.input_tokens)}  "
            f"cached {format_tokens(state.cached_input_tokens)}  "
            f"out {format_tokens(state.output_tokens)}  "
            f"reasoning {format_tokens(state.reasoning_output_tokens)}  "
            f"all {format_tokens(state.total_tokens)}",
            style="dim",
        ),
        Text(
            "Primary limit  "
            f"{state.rate_limits.primary_used_pct:.0f}% used  "
            f"| {_format_reset_countdown(state.rate_limits.primary_resets_at)}",
            style="yellow",
        ),
        Text(
            "Weekly limit  "
            f"{state.rate_limits.secondary_used_pct:.0f}% used  "
            f"| {_format_reset_countdown(state.rate_limits.secondary_resets_at)}",
            style="yellow",
        ),
    )
    return Panel(
        body,
        title=f"Codex ({feature_version('codex_context_split')})",
        border_style=_panel_border_style(state.context_fill_pct),
    )


def make_today_panel(state: HistoricalState) -> Panel:
    """Build the daily activity summary panel."""
    today_activity = state.today_activity
    today_tokens = state.today_tokens

    table = Table.grid(expand=True)
    table.add_column(ratio=3)
    table.add_column(ratio=2)

    activity_values = [entry.message_count for entry in state.daily_activity[-24:]]
    spark = sparkline(activity_values)

    if today_tokens:
        model_parts = [
            f"{model}: {format_tokens(tokens)}"
            for model, tokens in sorted(today_tokens.tokens_by_model.items())
        ]
        tokens_line = "  ".join(model_parts) if model_parts else "No tokens recorded today"
    else:
        tokens_line = "No token totals for today yet"

    if today_activity:
        activity_line = (
            f"Messages {format_tokens(today_activity.message_count)}  "
            f"Sessions {format_tokens(today_activity.session_count)}  "
            f"Tool calls {format_tokens(today_activity.tool_call_count)}"
        )
    else:
        activity_line = "No Claude activity recorded today"

    codex_recent = state.recent_codex_sessions[:3]
    codex_line = (
        "Recent Codex  "
        + "  ".join(
            f"{session.session_id[:8]} {session.model or 'unknown'} {format_tokens(session.tokens_used)}"
            for session in codex_recent
        )
        if codex_recent
        else "Recent Codex  No recent sessions"
    )

    table.add_row(Text(tokens_line, style="green"), Text(activity_line, style="cyan"))
    table.add_row(Text(f"Activity  {spark}", style="bold"), Text(codex_line, style="dim"))

    return Panel(table, title="Today", border_style="bright_blue")


def make_history_panel(state: HistoricalState) -> Panel:
    """Build a toggled comparison/history panel."""
    claude_table = Table(title="Claude Recent Days", expand=True, box=None)
    claude_table.add_column("Date", style="cyan", no_wrap=True)
    claude_table.add_column("Msgs", justify="right")
    claude_table.add_column("Sessions", justify="right")
    claude_table.add_column("Tools", justify="right")

    recent_days = state.daily_activity[-HISTORY_PANEL_ROWS:]
    if recent_days:
        for entry in reversed(recent_days):
            claude_table.add_row(
                entry.date,
                format_tokens(entry.message_count),
                format_tokens(entry.session_count),
                format_tokens(entry.tool_call_count),
            )
    else:
        claude_table.add_row("No data", "-", "-", "-")

    codex_table = Table(title="Recent Codex Sessions", expand=True, box=None)
    codex_table.add_column("Session", style="cyan", no_wrap=True)
    codex_table.add_column("Model")
    codex_table.add_column("Tokens", justify="right")
    codex_table.add_column("Title", overflow="fold")

    recent_sessions = state.recent_codex_sessions[:HISTORY_PANEL_ROWS]
    if recent_sessions:
        for session in recent_sessions:
            codex_table.add_row(
                session.session_id[:8],
                session.model or "unknown",
                format_tokens(session.tokens_used),
                session.title or session.cwd or "-",
            )
    else:
        codex_table.add_row("No data", "-", "-", "-")

    body = Group(claude_table, Text(""), codex_table)
    return Panel(
        body,
        title=f"History ({feature_version('history_toggle')})",
        border_style="bright_blue",
    )


def make_events_panel(events: list[EventLogEntry], *, limit: int = 10) -> Panel:
    """Build the recent event log panel."""
    table = Table.grid(expand=True)
    table.add_column(width=8)
    table.add_column(width=3)
    table.add_column(width=9)
    table.add_column(ratio=1)

    recent = events[-limit:]
    if not recent:
        table.add_row("--:--:--", "·", "INFO", "Waiting for events")
    else:
        for entry in recent:
            symbol, color = LEVEL_STYLES.get(entry.level, ("•", "white"))
            table.add_row(
                entry.timestamp.strftime("%H:%M:%S"),
                f"[{color}]{symbol}[/{color}]",
                f"[{color}]{entry.level:<7}[/{color}]",
                Text(entry.message, style=color),
            )

    return Panel(table, title=f"Events (last {limit})", border_style="white")
