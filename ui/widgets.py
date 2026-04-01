"""Reusable rich widgets for the dashboard UI."""

from __future__ import annotations

from rich.console import Group
from rich.progress import BarColumn, Progress, TextColumn
from rich.text import Text

from config import context_bar_color

BLOCKS = "▁▂▃▄▅▆▇█"


def format_tokens(value: int) -> str:
    """Format token counts with thousands separators."""
    return f"{value:,}"


def sparkline(values: list[int]) -> str:
    """Render a compact unicode sparkline from integer values."""
    if not values:
        return "No activity yet"

    if max(values) <= 0:
        return BLOCKS[0] * len(values)

    ceiling = max(values)
    result = []
    for value in values:
        index = round((value / ceiling) * (len(BLOCKS) - 1)) if value > 0 else 0
        result.append(BLOCKS[index])
    return "".join(result)


def context_bar(used_tokens: int, context_window: int, pct: float) -> Group:
    """Render a progress bar plus usage summary for a context window."""
    total = max(context_window, 1)
    used = max(0, min(used_tokens, total))
    color = context_bar_color(pct)

    progress = Progress(
        BarColumn(
            bar_width=None,
            complete_style=color,
            finished_style=color,
            style="grey23",
        ),
        TextColumn(f"[bold]{pct:>5.1f}%[/bold]", justify="right"),
        expand=True,
    )
    progress.add_task("context", total=total, completed=used)

    summary = Text(
        f"{format_tokens(used_tokens)} / {format_tokens(context_window)} tokens",
        style="dim",
    )

    return Group(progress, summary)
