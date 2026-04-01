"""Derived UI insights layered on top of the immutable dashboard state."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from config import (
    CRIT_THRESHOLD_PCT,
    HISTORY_PANEL_ROWS,
    MAX_UI_SAMPLES,
    STALE_UPDATE_SECONDS,
    TREND_DELTA_PCT,
    TREND_LOOKBACK_SECONDS,
    VELOCITY_LOOKBACK_SECONDS,
    WARN_THRESHOLD_PCT,
)
from state import ClaudeState, CodexState, DashboardState, HistoricalState


@dataclass(frozen=True)
class SnapshotSample:
    """One sampled dashboard point for trend and velocity calculations."""

    timestamp: datetime
    claude_fill_pct: float
    claude_context_tokens: int
    claude_output_total: int
    codex_fill_pct: float
    codex_context_tokens: int
    codex_total_tokens: int


@dataclass
class DashboardUIState:
    """Mutable UI-only state kept outside the collector-owned dashboard state."""

    show_history: bool = False
    samples: deque[SnapshotSample] = field(default_factory=lambda: deque(maxlen=MAX_UI_SAMPLES))

    def record_snapshot(self, state: DashboardState) -> None:
        claude, codex, _, _ = state.snapshot()
        self.samples.append(
            SnapshotSample(
                timestamp=datetime.now(),
                claude_fill_pct=claude.context_fill_pct,
                claude_context_tokens=claude.total_context_tokens,
                claude_output_total=claude.output_tokens_total,
                codex_fill_pct=codex.context_fill_pct,
                codex_context_tokens=codex.current_context_tokens or codex.total_tokens,
                codex_total_tokens=codex.total_tokens,
            )
        )

    def toggle_history(self) -> None:
        self.show_history = not self.show_history


@dataclass(frozen=True)
class SessionInsights:
    """Computed operational hints for one live session."""

    headroom_tokens: int
    age_seconds: int
    age_label: str
    trend_symbol: str
    trend_label: str
    velocity_tokens_per_min: float
    velocity_label: str
    is_stale: bool
    stale_label: str
    status_label: str
    status_style: str


@dataclass(frozen=True)
class DashboardInsights:
    """Top-level computed UI insights used by the renderer."""

    claude: SessionInsights
    codex: SessionInsights
    status_line: str
    controls_line: str
    show_history: bool


def _format_age(seconds: int) -> str:
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    mins, rem = divmod(seconds, 60)
    if mins < 60:
        return f"{mins}m {rem}s ago"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m ago"


def _format_velocity(tokens_per_min: float, label: str) -> str:
    if tokens_per_min <= 0:
        return f"{label} warming"
    return f"{label} +{int(round(tokens_per_min)):,}/min"


def _recent_window(samples: deque[SnapshotSample], seconds: int) -> list[SnapshotSample]:
    if not samples:
        return []
    newest = samples[-1].timestamp
    return [sample for sample in samples if (newest - sample.timestamp).total_seconds() <= seconds]


def _derive_trend(samples: list[SnapshotSample], *, attr: str) -> tuple[str, str]:
    if len(samples) < 2:
        return "→", "warming"

    start = getattr(samples[0], attr)
    end = getattr(samples[-1], attr)
    delta = end - start

    if delta >= TREND_DELTA_PCT * 2:
        return "↑", "rising fast"
    if delta >= TREND_DELTA_PCT:
        return "↗", "rising"
    if delta <= -(TREND_DELTA_PCT * 2):
        return "↓", "falling fast"
    if delta <= -TREND_DELTA_PCT:
        return "↘", "easing"
    return "→", "stable"


def _derive_velocity(samples: list[SnapshotSample], *, attr: str, label: str) -> tuple[float, str]:
    if len(samples) < 2:
        return 0.0, f"{label} warming"

    oldest = samples[0]
    newest = samples[-1]
    elapsed_seconds = max(1.0, (newest.timestamp - oldest.timestamp).total_seconds())
    delta = max(0, getattr(newest, attr) - getattr(oldest, attr))
    per_min = delta / elapsed_seconds * 60.0
    return per_min, _format_velocity(per_min, label)


def _derive_session_insights(
    *,
    is_active: bool,
    fill_pct: float,
    context_window: int,
    used_tokens: int,
    last_updated: datetime,
    trend_samples: list[SnapshotSample],
    trend_attr: str,
    velocity_samples: list[SnapshotSample],
    velocity_attr: str,
    velocity_label: str,
) -> SessionInsights:
    age_seconds = max(0, int((datetime.now() - last_updated).total_seconds()))
    is_stale = is_active and age_seconds >= STALE_UPDATE_SECONDS
    trend_symbol, trend_label = _derive_trend(trend_samples, attr=trend_attr)
    velocity_value, velocity_text = _derive_velocity(
        velocity_samples,
        attr=velocity_attr,
        label=velocity_label,
    )

    if not is_active:
        status_label = "idle"
        status_style = "dim"
    elif is_stale:
        status_label = "stale"
        status_style = "yellow"
    elif fill_pct >= CRIT_THRESHOLD_PCT:
        status_label = "critical"
        status_style = "bold red"
    elif fill_pct >= WARN_THRESHOLD_PCT:
        status_label = "warning"
        status_style = "yellow"
    elif "rising" in trend_label:
        status_label = trend_label
        status_style = "cyan"
    else:
        status_label = "healthy"
        status_style = "green"

    stale_label = "No recent updates" if is_stale else "Feed healthy"
    return SessionInsights(
        headroom_tokens=max(0, context_window - used_tokens),
        age_seconds=age_seconds,
        age_label=_format_age(age_seconds),
        trend_symbol=trend_symbol,
        trend_label=trend_label,
        velocity_tokens_per_min=velocity_value,
        velocity_label=velocity_text,
        is_stale=is_stale,
        stale_label=stale_label,
        status_label=status_label,
        status_style=status_style,
    )


def derive_dashboard_insights(
    claude: ClaudeState,
    codex: CodexState,
    historical: HistoricalState,
    ui_state: DashboardUIState,
) -> DashboardInsights:
    """Compute panel-level and header-level insights from state plus local samples."""
    trend_samples = _recent_window(ui_state.samples, TREND_LOOKBACK_SECONDS)
    velocity_samples = _recent_window(ui_state.samples, VELOCITY_LOOKBACK_SECONDS)

    claude_insights = _derive_session_insights(
        is_active=claude.is_active,
        fill_pct=claude.context_fill_pct,
        context_window=claude.context_window,
        used_tokens=claude.total_context_tokens,
        last_updated=claude.last_updated,
        trend_samples=trend_samples,
        trend_attr="claude_fill_pct",
        velocity_samples=velocity_samples,
        velocity_attr="claude_output_total",
        velocity_label="Output",
    )
    codex_used_tokens = codex.current_context_tokens or codex.total_tokens
    codex_insights = _derive_session_insights(
        is_active=codex.is_active,
        fill_pct=codex.context_fill_pct,
        context_window=codex.context_window,
        used_tokens=codex_used_tokens,
        last_updated=codex.last_updated,
        trend_samples=trend_samples,
        trend_attr="codex_fill_pct",
        velocity_samples=velocity_samples,
        velocity_attr="codex_total_tokens",
        velocity_label="Session",
    )

    status_line = (
        f"Status  Claude {claude_insights.status_label}  "
        f"| Codex {codex_insights.status_label}  "
        f"| History {'on' if ui_state.show_history else 'off'}"
    )
    controls_line = (
        "Keys  q quit  "
        "| h toggle history  "
        f"| Claude {claude_insights.age_label}  "
        f"| Codex {codex_insights.age_label}"
    )

    return DashboardInsights(
        claude=claude_insights,
        codex=codex_insights,
        status_line=status_line,
        controls_line=controls_line,
        show_history=ui_state.show_history,
    )
