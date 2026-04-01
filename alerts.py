"""
alerts.py — Threshold monitoring and event generation.

check_thresholds() is called by the render loop on every UI tick.
It compares current fill percentages against WARN_THRESHOLD_PCT and
CRIT_THRESHOLD_PCT and appends events only when a threshold is first crossed
(not on every tick). It also fires a macOS sound alert at critical level.

The "fired" flags live on DashboardState so they survive across ticks.
They reset automatically when the fill drops below the threshold (e.g., after
/compact is run).
"""

from __future__ import annotations

import subprocess
import logging
from datetime import datetime

from config import CRIT_THRESHOLD_PCT, MAX_EVENT_LOG_ENTRIES, WARN_THRESHOLD_PCT
from state import DashboardState, EventLogEntry

logger = logging.getLogger(__name__)

# macOS system sound for critical alerts
_MACOS_ALERT_SOUND = "/System/Library/Sounds/Glass.aiff"


def _play_sound() -> None:
    """Fire a non-blocking macOS sound alert. Silently no-ops on other platforms."""
    try:
        subprocess.Popen(
            ["afplay", _MACOS_ALERT_SOUND],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        pass  # Not on macOS or afplay unavailable


def _add_event(state: DashboardState, level: str, message: str, source: str) -> None:
    state.add_event(
        EventLogEntry(
            timestamp=datetime.now(),
            level=level,
            message=message,
            source=source,
        ),
        max_entries=MAX_EVENT_LOG_ENTRIES,
    )


def check_thresholds(state: DashboardState) -> None:
    """
    Compare current context fill percentages against thresholds and emit
    events when thresholds are first crossed. Resets fired flags on recovery.

    Must be called from the render loop (main thread). Acquires state.lock
    internally via snapshot() and direct attribute access.
    """
    with state.lock:
        claude = state.claude
        codex = state.codex

        # ---- Claude ----
        claude_pct = claude.context_fill_pct

        if claude_pct >= CRIT_THRESHOLD_PCT:
            if not state._claude_crit_fired:
                state._claude_crit_fired = True
                state._claude_warn_fired = True  # warn is implicitly fired too
        elif claude_pct >= WARN_THRESHOLD_PCT:
            if not state._claude_warn_fired:
                state._claude_warn_fired = True
        else:
            # Recovery — reset both flags
            state._claude_warn_fired = False
            state._claude_crit_fired = False

        # ---- Codex ----
        codex_pct = codex.context_fill_pct

        if codex_pct >= CRIT_THRESHOLD_PCT:
            if not state._codex_crit_fired:
                state._codex_crit_fired = True
                state._codex_warn_fired = True
        elif codex_pct >= WARN_THRESHOLD_PCT:
            if not state._codex_warn_fired:
                state._codex_warn_fired = True
        else:
            state._codex_warn_fired = False
            state._codex_crit_fired = False

        # Capture what needs to be emitted (outside lock re-entry)
        pending_claude_warn = claude_pct >= WARN_THRESHOLD_PCT and not getattr(state, "_claude_warn_emitted", False)
        pending_claude_crit = claude_pct >= CRIT_THRESHOLD_PCT and not getattr(state, "_claude_crit_emitted", False)
        pending_codex_warn = codex_pct >= WARN_THRESHOLD_PCT and not getattr(state, "_codex_warn_emitted", False)
        pending_codex_crit = codex_pct >= CRIT_THRESHOLD_PCT and not getattr(state, "_codex_crit_emitted", False)

    # Emit events (add_event acquires its own lock slice — safe after we released above)
    if pending_claude_warn and not pending_claude_crit:
        _add_event(
            state,
            "WARNING",
            f"Claude context at {claude_pct:.0f}% ({claude.total_context_tokens:,}/{claude.context_window:,} tokens) — consider running /compact",
            "claude",
        )
        with state.lock:
            state._claude_warn_emitted = True  # type: ignore[attr-defined]

    if pending_claude_crit:
        _add_event(
            state,
            "ERROR",
            f"Claude context CRITICAL at {claude_pct:.0f}% ({claude.total_context_tokens:,}/{claude.context_window:,} tokens) — run /compact NOW",
            "claude",
        )
        with state.lock:
            state._claude_crit_emitted = True  # type: ignore[attr-defined]
            state._claude_warn_emitted = True  # type: ignore[attr-defined]
        _play_sound()

    if pending_codex_warn and not pending_codex_crit:
        _add_event(
            state,
            "WARNING",
            f"Codex context at {codex_pct:.0f}% ({codex.total_tokens:,}/{codex.context_window:,} tokens)",
            "codex",
        )
        with state.lock:
            state._codex_warn_emitted = True  # type: ignore[attr-defined]

    if pending_codex_crit:
        _add_event(
            state,
            "ERROR",
            f"Codex context CRITICAL at {codex_pct:.0f}% ({codex.total_tokens:,}/{codex.context_window:,} tokens)",
            "codex",
        )
        with state.lock:
            state._codex_crit_emitted = True  # type: ignore[attr-defined]
            state._codex_warn_emitted = True  # type: ignore[attr-defined]
        _play_sound()

    # Reset emitted flags on recovery (so future crossings get re-emitted)
    with state.lock:
        if claude_pct < WARN_THRESHOLD_PCT:
            state._claude_warn_emitted = False  # type: ignore[attr-defined]
            state._claude_crit_emitted = False  # type: ignore[attr-defined]
        if codex_pct < WARN_THRESHOLD_PCT:
            state._codex_warn_emitted = False  # type: ignore[attr-defined]
            state._codex_crit_emitted = False  # type: ignore[attr-defined]
