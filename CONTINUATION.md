# AI Dashboard — Continuation Guide

> Hand this file to Claude at the start of the next session.
> It contains everything needed to pick up exactly where Phase 1 left off.

---

## What This Project Is

A terminal dashboard (TUI) that monitors live usage of **Claude Code** and **OpenAI Codex** — context window fill %, token counts, rate limits, daily history, and error/warning events — using only local files (no API calls).

Run it in a second terminal tab alongside a Claude Code or Codex session.

---

## Current Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | COMPLETE | Data collectors, state, alerts — 71/71 tests pass |
| Phase 2 | NOT STARTED | UI layout with `rich.live.Live` + mock data |
| Phase 3 | NOT STARTED | Wire live data into UI |
| Phase 4 | NOT STARTED | Polish: sparklines, keyboard shortcuts, sound alert |

---

## Project Location

```
/Users/dhanushchandra/Downloads/Vibe_Coding_Projects/ai_dashboard/
```

Run tests to verify everything is still green before starting Phase 2:
```bash
cd /Users/dhanushchandra/Downloads/Vibe_Coding_Projects/ai_dashboard
python3 -m pytest tests/ -v
# Expected: 71 passed
```

---

## File Structure (Phase 1 output)

```
ai_dashboard/
├── config.py                   — All constants, paths, thresholds, color styles
├── state.py                    — Immutable dataclasses for all state
├── alerts.py                   — Threshold logic (70%/90%), dedup, macOS sound
├── requirements.txt
├── collectors/
│   ├── claude_collector.py     — Watchdog tailing of Claude session JSONL
│   ├── codex_collector.py      — Watchdog tailing of Codex rollout JSONL + SQLite poll
│   └── stats_collector.py      — 60s poll of stats-cache.json
├── tests/
│   ├── test_claude_collector.py
│   ├── test_codex_collector.py
│   ├── test_alerts.py
│   └── test_stats_collector.py
└── ui/                         — EMPTY — to be built in Phase 2
    (renderer.py, panels.py, widgets.py, __init__.py)
```

---

## Data Sources (confirmed from live files)

### Claude Code

| Source | Path | What it provides |
|--------|------|-----------------|
| Session JSONL | `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` | Per-message usage blocks |
| Stats cache | `~/.claude/stats-cache.json` | Daily activity + token totals by model |

**Session JSONL structure** — each assistant message has:
```json
{
  "message": {
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "usage": {
      "input_tokens": 1,
      "cache_read_input_tokens": 92939,
      "cache_creation_input_tokens": 785,
      "output_tokens": 521
    }
  }
}
```

**CRITICAL insight**: Do NOT sum `cache_read_input_tokens` across turns. Each turn's cache read includes the full conversation history, so summing = multiplying it. Use the **last turn's** token counts for context fill %. Cumulative sums are only meaningful for `output_tokens` and `cache_creation_input_tokens`.

**Context window**: Not in the file. Look up by model name in `config.CONTEXT_WINDOWS`. All current Claude models = 200,000.

### Codex

| Source | Path | What it provides |
|--------|------|-----------------|
| Rollout JSONL | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | Real-time token counts + `model_context_window` |
| SQLite | `~/.codex/state_5.sqlite` → `threads` table | Session metadata, `tokens_used`, model, title |

**Rollout JSONL structure** — token_count events:
```json
{
  "type": "event_msg",
  "payload": {
    "type": "token_count",
    "info": {
      "total_token_usage": {
        "input_tokens": 8781,
        "cached_input_tokens": 7680,
        "output_tokens": 40,
        "reasoning_output_tokens": 24,
        "total_tokens": 8821
      },
      "model_context_window": 258400
    },
    "rate_limits": {
      "primary": { "used_percent": 1.0, "window_minutes": 300, "resets_at": 1774925054 },
      "secondary": { "used_percent": 0.0, "window_minutes": 10080, "resets_at": 1775511854 }
    }
  }
}
```

Codex gives you the **exact** `model_context_window` (258,400 for gpt-5.4). Use `total_tokens / model_context_window` for fill %.

### Live Data Confirmed (as of 2026-03-31)

- **Claude**: session `5a72839e`, model `claude-sonnet-4-6`, context 46.9% (93,725 / 200,000)
- **Codex**: latest session `019d40dd`, model `gpt-5.4`, 17,630 tokens
- **Historical**: 2,157 messages on 2026-03-16; total tokens 66,505

---

## Key State Dataclasses (state.py)

### ClaudeState

```python
@dataclass(frozen=True)
class ClaudeState:
    session_id: str = ""
    model: str = ""
    context_window: int = 200_000

    # LAST TURN only — drives context fill %
    latest_input_tokens: int = 0
    latest_cache_read_tokens: int = 0
    latest_cache_creation_tokens: int = 0
    latest_output_tokens: int = 0

    # Cumulative session — drives stats panel
    output_tokens_total: int = 0
    cache_creation_total: int = 0

    session_file: str = ""
    last_updated: datetime = ...

    @property total_context_tokens -> int  # sum of latest_ fields for fill
    @property context_fill_pct -> float   # 0.0–100.0
    @property is_active -> bool
```

### CodexState

```python
@dataclass(frozen=True)
class CodexState:
    session_id: str = ""
    model: str = ""
    context_window: int = 258_400

    # From token_count events (cumulative within session — Codex reports running totals)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    rate_limits: CodexRateLimits = ...
    session_file: str = ""
    last_updated: datetime = ...

    @property context_fill_pct -> float
    @property is_active -> bool
```

### CodexRateLimits

```python
@dataclass(frozen=True)
class CodexRateLimits:
    primary_used_pct: float = 0.0
    primary_window_minutes: int = 300
    primary_resets_at: int = 0     # Unix timestamp

    secondary_used_pct: float = 0.0
    secondary_window_minutes: int = 10080
    secondary_resets_at: int = 0
```

### DashboardState (shared mutable container)

```python
class DashboardState:
    lock: threading.Lock
    claude: ClaudeState
    codex: CodexState
    historical: HistoricalState
    events: list[EventLogEntry]

    def update_claude(new: ClaudeState) -> None
    def update_codex(new: CodexState) -> None
    def update_historical(new: HistoricalState) -> None
    def add_event(entry: EventLogEntry, max_entries=100) -> None
    def snapshot() -> tuple[ClaudeState, CodexState, HistoricalState, list[EventLogEntry]]
```

---

## Key Config Values (config.py)

```python
WARN_THRESHOLD_PCT = 70.0      # yellow warning
CRIT_THRESHOLD_PCT = 90.0      # red critical + sound
UI_REFRESH_SECONDS = 2.0       # render loop interval
STATS_POLL_SECONDS = 60        # stats-cache.json poll
CODEX_DB_POLL_SECONDS = 5      # SQLite poll

LEVEL_STYLES = {
    "INFO":    ("●", "bright_blue"),
    "WARNING": ("⚠", "yellow"),
    "ERROR":   ("✗", "bold red"),
    "SUCCESS": ("✓", "green"),
    "METRIC":  ("◆", "cyan"),
}
```

---

## Phase 2 — What to Build Next

### Goal
Build the UI layout using `rich.live.Live` + `rich.layout.Layout`. Start with **mock/hardcoded data** so layout is validated before wiring live collectors.

### Tech stack
- `rich.live.Live` — renders a layout object every 2 seconds
- `rich.layout.Layout` — splits terminal into named regions
- `rich.panel.Panel` — boxes with titles
- `rich.progress.Progress` — the context bar
- `rich.table.Table` — event log, stats

No `textual`, no new installs. Everything is in `rich` 13.7.1 which is already installed.

### Files to create in `ui/`

```
ui/__init__.py
ui/widgets.py    — context_bar(), sparkline(), format_tokens()
ui/panels.py     — make_claude_panel(), make_codex_panel(), make_today_panel(), make_events_panel()
ui/renderer.py   — build_layout(state) -> rich.layout.Layout
```

And the entry point:
```
main.py          — starts collectors, runs rich.live.Live render loop, handles q-to-quit
```

### Target layout

```
┌─────────────────────────── HEADER ────────────────────────────┐
│   AI Usage Dashboard  ●Claude Code  ●Codex  [refresh: 2s]    │
├────────────────────────┬──────────────────────────────────────┤
│  CLAUDE CODE           │  CODEX                               │
│  Session / Model       │  Session / Model                     │
│  Context Window Bar    │  Context Window Bar                  │
│  ████████░░░░░ 47%     │  ████░░░░░░░░░░░░░ 17%              │
│  93,725 / 200,000      │  44,310 / 258,400                    │
│  Output this session   │  Rate Limits: 5hr 1%  Weekly 0%     │
├────────────────────────┴──────────────────────────────────────┤
│  TODAY                                                         │
│  Claude: out 42,145  cache_created 239,464                    │
│  Activity: ▁▃▇█▅▂▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁                         │
├───────────────────────────────────────────────────────────────┤
│  EVENTS (last 10)                                              │
│  21:32  ⚠ WARNING  Claude context at 47%                      │
│  21:29  ● INFO     Claude session active: 5a72839e            │
└───────────────────────────────────────────────────────────────┘
```

### Context bar color rules

| Fill % | Border color | Bar color |
|--------|-------------|-----------|
| 0–49   | default     | green     |
| 50–69  | default     | dark_orange |
| 70–89  | yellow      | yellow    |
| 90–100 | bold red    | bold red  |

When Claude hits 70%: border turns yellow AND event log shows "consider running /compact".

### Rate limit countdown (Codex only)

`primary_resets_at` is a Unix timestamp. Display as:
```python
from datetime import datetime
delta = primary_resets_at - int(datetime.now().timestamp())
hours, rem = divmod(delta, 3600)
mins = rem // 60
f"Resets in {hours}h {mins}m"
```

### Sparkline (Today panel)

Use Unicode block chars from `dailyActivity`:
```python
BLOCKS = "▁▂▃▄▅▆▇█"
# Scale message_count for each day to 0–7 index
```

---

## Phase 3 — Wire Data to UI

In `main.py`:
1. Create `DashboardState()`
2. Start all collectors: `ClaudeCollector`, `CodexCollector`, `StatsCollector`
3. Start `rich.live.Live` with `build_layout(state)` refreshing every 2s
4. On each tick, call `alerts.check_thresholds(state)` before rendering

```python
from rich.live import Live
from collectors.claude_collector import ClaudeCollector
from collectors.codex_collector import CodexCollector
from collectors.stats_collector import StatsCollector
from state import DashboardState
from alerts import check_thresholds
from ui.renderer import build_layout

state = DashboardState()
collectors = [ClaudeCollector(state), CodexCollector(state), StatsCollector(state)]
for c in collectors: c.start()

with Live(build_layout(state), refresh_per_second=0.5, screen=True) as live:
    while True:
        check_thresholds(state)
        live.update(build_layout(state))
        time.sleep(2.0)
```

---

## Phase 4 — Polish

- Keyboard `q` to quit (use `rich` + `sys.stdin` or `keyboard` lib)
- Press `h` to toggle session history panel (last 10 Claude + Codex sessions)
- macOS sound at 90%: already implemented in `alerts._play_sound()` using `afplay`
- Graceful handling when no session is active (show "No active session" placeholder)
- Auto-detect terminal width: collapse to single column below 100 chars wide

---

## Packages (all pre-installed)

```
rich==13.7.1       # TUI rendering
watchdog==2.1.6    # File system events
psutil==5.8.0      # System stats (bonus panel if desired)
# sqlite3, threading, json, pathlib — stdlib
```

```bash
pip install -r requirements.txt   # already satisfied, but run to confirm
```

---

## Known Limitations

1. **Claude context window size is approximate** — inferred from model name (`config.CONTEXT_WINDOWS`), not returned by the API. 200,000 for all current Claude 4.x models.
2. **Claude cost always shows $0** — `stats-cache.json` has `costUSD: 0` on subscription plans. Display tokens, not cost.
3. **Codex context fill is exact** — `model_context_window: 258400` comes directly from rollout events.
4. **Codex historical tokens** — `threads.tokens_used` in SQLite is the total for the whole session, not per-turn breakdown.
5. **Watchdog on macOS uses FSEvents** — ~1-2s coalescing delay. Normal for a monitoring tool.

---

## How to Tell Claude to Continue

Paste this at the start of the next session:

> "I'm building an AI usage dashboard TUI. Phase 1 is complete — read CONTINUATION.md at /Users/dhanushchandra/Downloads/Vibe_Coding_Projects/ai_dashboard/CONTINUATION.md and proceed with Phase 2: build the rich.live.Live UI layout in ui/widgets.py, ui/panels.py, and ui/renderer.py. Use mock data first. Then build main.py and wire everything together in Phase 3."
