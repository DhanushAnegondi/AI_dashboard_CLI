"""Version registry for optional dashboard features."""

from __future__ import annotations

FEATURE_VERSIONS = {
    "headroom": "v1.0.0",
    "activity_age": "v1.0.0",
    "context_trend": "v1.0.0",
    "stale_watch": "v1.0.0",
    "rate_limit_detail": "v1.0.0",
    "session_velocity": "v1.0.0",
    "header_status": "v1.0.0",
    "history_toggle": "v1.0.0",
    "codex_context_split": "v2.0.0",
}


def feature_version(name: str) -> str:
    """Return the version string for a named feature."""
    return FEATURE_VERSIONS.get(name, "unversioned")
