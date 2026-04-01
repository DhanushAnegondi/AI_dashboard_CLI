# AI Dashboard CLI

A terminal dashboard for monitoring local **Claude Code** and **OpenAI Codex** usage from files already present on your machine.

It is designed to run in a second terminal tab while you work, so you can keep an eye on:

- context window fill
- token usage
- rate limits
- daily Claude history
- recent events and threshold warnings

This project is intentionally **local-file only**. It does not call Anthropic or OpenAI APIs directly.

## What It Reads

### Claude

- Session JSONL files under `~/.claude/projects/...`
- Daily stats from `~/.claude/stats-cache.json`

### Codex

- Live rollout JSONL files under `~/.codex/sessions/...`
- Session metadata from `~/.codex/state_5.sqlite`

## Repo Branches

Two feature branches are available in GitHub:

- `feature/revert-to-phase2-phase3`
  - The initial Phase 2 / Phase 3 baseline
  - Simple live layout, mock mode, and collector wiring

- `feature/rich-dashboard-upgrades`
  - Adds richer operational UI
  - Headroom, trend hints, velocity, stale-feed warnings, history toggle, and feature versioning

If you want the simpler baseline, use the first branch. If you want the more advanced operator-style dashboard, use the second.

## Project Structure

```text
ai_dashboard/
├── alerts.py
├── config.py
├── main.py
├── state.py
├── collectors/
│   ├── claude_collector.py
│   ├── codex_collector.py
│   └── stats_collector.py
├── ui/
│   ├── widgets.py
│   ├── panels.py
│   └── renderer.py
└── tests/
```

## How It Works

The workflow is simple:

1. `main.py` creates a shared `DashboardState`
2. background collectors watch local Claude and Codex files
3. collectors parse new data and replace immutable state snapshots
4. the render loop calls `build_layout(...)` every few seconds
5. `rich.live.Live` redraws the terminal UI
6. `alerts.check_thresholds(...)` emits warnings when usage crosses thresholds

### Data Flow

```text
local files -> collectors -> DashboardState -> renderer -> rich.live.Live
```

## Installation

From the project directory:

```bash
cd /Users/dhanushchandra/Downloads/Vibe_Coding_Projects/ai_dashboard
python3 -m pip install --user -r requirements.txt
```

Optional, if you want user-installed Python scripts like `pytest` on your PATH:

```bash
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
```

## Running The Dashboard

### Live mode

```bash
cd /Users/dhanushchandra/Downloads/Vibe_Coding_Projects/ai_dashboard
python3 main.py
```

### Mock mode

Use this to validate the layout without relying on live session files:

```bash
python3 main.py --mock
```

### Keyboard

- `q` quit

On the richer branch, there may be extra controls such as:

- `h` toggle history

## Running Tests

```bash
cd /Users/dhanushchandra/Downloads/Vibe_Coding_Projects/ai_dashboard
python3 -m pytest tests/ -q
```

## Current Architecture

### `state.py`

Defines the immutable dataclasses used throughout the app.

- `ClaudeState`
- `CodexState`
- `HistoricalState`
- `EventLogEntry`
- `DashboardState`

### `collectors/`

Each collector owns one source of truth:

- `claude_collector.py`
  - watches Claude session JSONL files
- `codex_collector.py`
  - watches Codex rollout JSONL files and polls SQLite metadata
- `stats_collector.py`
  - polls Claude daily historical stats

### `ui/`

- `widgets.py`
  - reusable rendering helpers such as token formatting and bars
- `panels.py`
  - builds user-facing panels
- `renderer.py`
  - composes the full layout

### `alerts.py`

Contains threshold logic and event emission for warning and critical states.

## How To Extend It

If you want to implement more features, the cleanest path is:

1. Keep collectors focused on parsing raw data only
2. Keep `state.py` limited to durable shared state
3. Add derived or presentation-only logic in `ui/`
4. Keep panel-specific rendering in `ui/panels.py`
5. Keep global layout composition in `ui/renderer.py`
6. Add tests whenever parsing or state semantics change

### Good Extension Ideas

- session browser
- project filter
- model filter
- aggregate usage across all sessions
- history explorer
- sparkline improvements
- export snapshots
- separate “current context” vs “session cumulative” views

## Important Notes

- Claude and Codex do not expose identical semantics
- Claude context is inferred from the latest assistant usage block
- Codex semantics may vary depending on which rollout fields are present
- This dashboard only knows about usage stored locally on this machine
- It is not an account-wide billing dashboard

## Git Workflow

Suggested workflow for future changes:

```bash
git checkout feature/rich-dashboard-upgrades
git checkout -b feature/my-new-improvement
python3 -m pytest tests/ -q
git add .
git commit -m "Add my improvement"
git push -u origin feature/my-new-improvement
```

If you want to compare against the simpler baseline:

```bash
git checkout feature/revert-to-phase2-phase3
```

## Development Tip

Keep `CONTINUATION.md` updated if you are building this iteratively with AI agents. It is already structured to help another session continue implementation cleanly.
