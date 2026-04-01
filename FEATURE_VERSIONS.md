# Dashboard Feature Versions

Use this file to split commits or PRs by feature without guessing which changes belong together.

| Feature | Version | Purpose | Primary Files |
|---|---|---|---|
| Headroom | `v1.0.0` | Remaining tokens shown for Claude and Codex | `ui/insights.py`, `ui/panels.py` |
| Activity Age | `v1.0.0` | Shows how fresh each feed is | `ui/insights.py`, `ui/panels.py`, `ui/renderer.py` |
| Context Trend | `v1.0.0` | Detects rising / stable / easing context usage | `ui/insights.py`, `ui/panels.py` |
| Stale Watch | `v1.0.0` | Flags active feeds with no updates for too long | `config.py`, `ui/insights.py`, `ui/panels.py`, `ui/renderer.py` |
| Rate Limit Detail | `v1.0.0` | Clearer Codex reset labels | `ui/panels.py` |
| Session Velocity | `v1.0.0` | Tokens-per-minute hints for Claude and Codex | `ui/insights.py`, `ui/panels.py` |
| Header Status | `v1.0.0` | Global health strip and controls hint | `ui/insights.py`, `ui/renderer.py` |
| History Toggle | `v1.0.0` | `h` toggles a comparison/history panel | `ui/insights.py`, `ui/panels.py`, `ui/renderer.py`, `main.py` |
| Codex Context Split | `v2.0.0` | Distinguishes current request context from session total | `state.py`, `collectors/codex_collector.py`, `ui/panels.py`, `alerts.py`, `tests/` |
