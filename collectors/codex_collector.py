"""
codex_collector.py — Tracks live Codex session data from two sources:

  1. ~/.codex/sessions/**/*.jsonl  (watchdog file tailing)
     Every token_count event gives real-time tokens + model_context_window
     + rate limit percentages.

  2. ~/.codex/state_5.sqlite  (polled every CODEX_DB_POLL_SECONDS)
     The `threads` table holds cumulative tokens_used per session,
     plus model, cwd, title.

The most recent rollout file drives the primary CodexState. The SQLite
poll enriches it with session metadata when the rollout is sparse.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from config import (
    ACTIVE_SESSION_WINDOW_SECONDS,
    CODEX_DB_POLL_SECONDS,
    CODEX_SESSIONS_DIR,
    CODEX_SQLITE_DB,
    MAX_EVENT_LOG_ENTRIES,
)
from state import CodexRateLimits, CodexSessionSummary, CodexState, DashboardState, EventLogEntry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_active_rollout(sessions_dir: Path) -> Path | None:
    """Return the most recently modified rollout-*.jsonl within the active window."""
    now = time.time()
    best: tuple[float, Path] | None = None

    if not sessions_dir.exists():
        return None

    for path in sessions_dir.rglob("rollout-*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= ACTIVE_SESSION_WINDOW_SECONDS:
            if best is None or mtime > best[0]:
                best = (mtime, path)

    return best[1] if best else None


def _session_id_from_rollout(path: Path) -> str:
    """
    Rollout filenames follow the pattern:
        rollout-<ISO-date>-<uuid>.jsonl
    We want the UUID portion (last 36 chars before .jsonl).
    """
    stem = path.stem  # e.g. rollout-2026-03-30T15-28-45-019d40dd-1bd2-73d1-996d-1354dfb16259
    parts = stem.split("-")
    # UUID is the last 5 groups: 8-4-4-4-12
    if len(parts) >= 5:
        return "-".join(parts[-5:])[:8]
    return stem[:8]


def _parse_token_count_event(raw: str) -> tuple[CodexState | None, CodexRateLimits | None]:
    """
    Parse one JSONL line from a Codex rollout file.
    Returns (CodexState, CodexRateLimits) if it's a token_count event,
    otherwise (None, None).
    """
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None, None

    payload = entry.get("payload", {})
    if payload.get("type") != "token_count":
        return None, None

    info = payload.get("info", {})
    total = info.get("total_token_usage", {})
    context_window = info.get("model_context_window", 258_400)

    rl_raw = payload.get("rate_limits", {})
    primary = rl_raw.get("primary", {})
    secondary = rl_raw.get("secondary", {})

    rate_limits = CodexRateLimits(
        primary_used_pct=primary.get("used_percent", 0.0),
        primary_window_minutes=primary.get("window_minutes", 300),
        primary_resets_at=primary.get("resets_at", 0),
        secondary_used_pct=secondary.get("used_percent", 0.0),
        secondary_window_minutes=secondary.get("window_minutes", 10080),
        secondary_resets_at=secondary.get("resets_at", 0),
    )

    # We return a partial CodexState; the caller fills session_id / model
    partial = CodexState(
        context_window=context_window,
        input_tokens=total.get("input_tokens", 0),
        cached_input_tokens=total.get("cached_input_tokens", 0),
        output_tokens=total.get("output_tokens", 0),
        reasoning_output_tokens=total.get("reasoning_output_tokens", 0),
        total_tokens=total.get("total_tokens", 0),
        rate_limits=rate_limits,
    )
    return partial, rate_limits


def _full_parse_rollout(path: Path) -> CodexState | None:
    """
    Read the entire rollout file and return the last token_count state seen.
    Returns None if no token_count events found.
    """
    last_partial: CodexState | None = None

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                partial, _ = _parse_token_count_event(raw)
                if partial:
                    last_partial = partial
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)

    return last_partial


def _query_sqlite_sessions(db_path: Path, limit: int = 10) -> list[CodexSessionSummary]:
    """Query the Codex SQLite DB for the most recent sessions."""
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, model, tokens_used, created_at, cwd, title
            FROM threads
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("SQLite query failed: %s", exc)
        return []

    return [
        CodexSessionSummary(
            session_id=row["id"] or "",
            model=row["model"] or "",
            tokens_used=row["tokens_used"] or 0,
            created_at=row["created_at"] or 0,
            cwd=row["cwd"] or "",
            title=row["title"] or "",
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Watchdog handler
# ---------------------------------------------------------------------------


class _CodexFileHandler(FileSystemEventHandler):
    def __init__(self, collector: "CodexCollector") -> None:
        super().__init__()
        self._collector = collector

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if "rollout-" not in path.name or path.suffix != ".jsonl":
            return
        self._collector.on_file_modified(path)

    def on_created(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if "rollout-" in path.name and path.suffix == ".jsonl":
            self._collector.on_new_session_file(path)


# ---------------------------------------------------------------------------
# SQLite poll worker
# ---------------------------------------------------------------------------


class _SqlitePollWorker(threading.Thread):
    """Background thread that polls the Codex SQLite DB on a fixed interval."""

    def __init__(self, collector: "CodexCollector") -> None:
        super().__init__(daemon=True, name="CodexSqlitePoller")
        self._collector = collector
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._collector.poll_sqlite()
            self._stop_event.wait(timeout=CODEX_DB_POLL_SECONDS)

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Main collector
# ---------------------------------------------------------------------------


class CodexCollector:
    """
    Monitors Codex session rollout files and SQLite DB.

    Lifecycle:
        collector = CodexCollector(dashboard_state)
        collector.start()
        ...
        collector.stop()
    """

    def __init__(self, state: DashboardState) -> None:
        self._state = state
        self._observer: Observer | None = None
        self._sqlite_worker: _SqlitePollWorker | None = None
        self._lock = threading.Lock()

        self._active_path: Path | None = None
        self._file_offset: int = 0
        self._current_state: CodexState = CodexState()

        # Latest DB session (for model/title enrichment)
        self._latest_db_session: CodexSessionSummary | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._initial_scan()

        handler = _CodexFileHandler(self)
        self._observer = Observer()
        if CODEX_SESSIONS_DIR.exists():
            self._observer.schedule(handler, str(CODEX_SESSIONS_DIR), recursive=True)
        self._observer.start()

        self._sqlite_worker = _SqlitePollWorker(self)
        self._sqlite_worker.start()

        logger.info("CodexCollector started, watching %s", CODEX_SESSIONS_DIR)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
        if self._sqlite_worker:
            self._sqlite_worker.stop()
        logger.info("CodexCollector stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _initial_scan(self) -> None:
        """Find the active rollout file and do a full parse."""
        # Initial DB poll for session metadata
        self.poll_sqlite()

        active = _find_active_rollout(CODEX_SESSIONS_DIR)
        if active is None:
            self._emit_event("INFO", "No active Codex session found", "codex")
            return

        self._switch_to_session(active)

    def _switch_to_session(self, path: Path) -> None:
        """Adopt a new rollout file as the active session."""
        with self._lock:
            session_id = _session_id_from_rollout(path)
            partial = _full_parse_rollout(path)

            model = self._latest_db_session.model if self._latest_db_session else ""

            if partial:
                new_state = CodexState(
                    session_id=session_id,
                    model=model,
                    context_window=partial.context_window,
                    input_tokens=partial.input_tokens,
                    cached_input_tokens=partial.cached_input_tokens,
                    output_tokens=partial.output_tokens,
                    reasoning_output_tokens=partial.reasoning_output_tokens,
                    total_tokens=partial.total_tokens,
                    rate_limits=partial.rate_limits,
                    session_file=str(path),
                    last_updated=datetime.now(),
                )
            else:
                new_state = CodexState(
                    session_id=session_id,
                    model=model,
                    session_file=str(path),
                    last_updated=datetime.now(),
                )

            self._current_state = new_state
            self._active_path = path

            try:
                self._file_offset = path.stat().st_size
            except OSError:
                self._file_offset = 0

            self._state.update_codex(new_state)

        self._emit_event("INFO", f"Codex session active: {session_id} ({model})", "codex")

    def on_new_session_file(self, path: Path) -> None:
        """Called when watchdog detects a brand-new rollout file."""
        self._emit_event("INFO", f"New Codex session started: {path.name}", "codex")
        self._switch_to_session(path)

    def on_file_modified(self, path: Path) -> None:
        """Called when watchdog detects a modification to a rollout file."""
        with self._lock:
            # If this is a different (newer) file, switch to it
            if self._active_path is not None and path != self._active_path:
                try:
                    active_mtime = self._active_path.stat().st_mtime if self._active_path.exists() else 0.0
                    new_mtime = path.stat().st_mtime
                except OSError:
                    return
                if new_mtime <= active_mtime:
                    return  # Older file — ignore

        if self._active_path is None or path != self._active_path:
            self._switch_to_session(path)
            return

        self._tail_and_update(path)

    def _tail_and_update(self, path: Path) -> None:
        """Read new bytes from the active rollout file and update state."""
        with self._lock:
            if path != self._active_path:
                return

            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(self._file_offset)
                    new_content = fh.read()
                    self._file_offset = fh.tell()
            except OSError as exc:
                logger.warning("Error tailing %s: %s", path, exc)
                return

            latest_partial: CodexState | None = None
            for raw in new_content.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                partial, _ = _parse_token_count_event(raw)
                if partial:
                    latest_partial = partial

            if latest_partial is None:
                return

            # Build new state preserving session metadata
            prev = self._current_state
            new_state = CodexState(
                session_id=prev.session_id,
                model=prev.model or (self._latest_db_session.model if self._latest_db_session else ""),
                context_window=latest_partial.context_window,
                input_tokens=latest_partial.input_tokens,
                cached_input_tokens=latest_partial.cached_input_tokens,
                output_tokens=latest_partial.output_tokens,
                reasoning_output_tokens=latest_partial.reasoning_output_tokens,
                total_tokens=latest_partial.total_tokens,
                rate_limits=latest_partial.rate_limits,
                session_file=str(path),
                last_updated=datetime.now(),
            )
            self._current_state = new_state
            self._state.update_codex(new_state)

    def poll_sqlite(self) -> None:
        """Refresh session metadata from SQLite (called by the poll worker thread)."""
        sessions = _query_sqlite_sessions(CODEX_SQLITE_DB, limit=10)
        if not sessions:
            return

        with self._lock:
            self._latest_db_session = sessions[0]
            # If we have an active state but no model yet, back-fill it
            if self._current_state.is_active and not self._current_state.model:
                new_state = CodexState(
                    session_id=self._current_state.session_id,
                    model=sessions[0].model,
                    context_window=self._current_state.context_window,
                    input_tokens=self._current_state.input_tokens,
                    cached_input_tokens=self._current_state.cached_input_tokens,
                    output_tokens=self._current_state.output_tokens,
                    reasoning_output_tokens=self._current_state.reasoning_output_tokens,
                    total_tokens=self._current_state.total_tokens,
                    rate_limits=self._current_state.rate_limits,
                    session_file=self._current_state.session_file,
                    last_updated=datetime.now(),
                )
                self._current_state = new_state
                self._state.update_codex(new_state)

        # Push the recent sessions list to historical state
        from state import CodexSessionSummary as CSS  # local import to avoid circularity
        with self._state.lock:
            old_hist = self._state.historical
            from state import HistoricalState
            new_hist = HistoricalState(
                daily_activity=old_hist.daily_activity,
                daily_model_tokens=old_hist.daily_model_tokens,
                recent_codex_sessions=sessions,
                last_updated=datetime.now(),
            )
            self._state.historical = new_hist

    def _emit_event(self, level: str, message: str, source: str) -> None:
        self._state.add_event(
            EventLogEntry(
                timestamp=datetime.now(),
                level=level,
                message=message,
                source=source,
            ),
            max_entries=MAX_EVENT_LOG_ENTRIES,
        )
