"""
claude_collector.py — Watches ~/.claude/projects/**/*.jsonl for live session data.

Strategy:
  1. On start, find the most recently modified .jsonl under CLAUDE_PROJECTS_DIR
     that was touched within ACTIVE_SESSION_WINDOW_SECONDS.
  2. Seek to end of file (we only care about new tokens, not replaying history).
     Exception: if a new session file is detected, do a full parse from byte 0
     so the cumulative session totals are correct.
  3. A watchdog FileSystemEventHandler fires on every file modification.
     We read only the new bytes (byte-offset tailing) and parse JSONL lines.
  4. Each assistant message with a `usage` block updates ClaudeState via
     DashboardState.update_claude().
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from config import (
    ACTIVE_SESSION_WINDOW_SECONDS,
    CLAUDE_PROJECTS_DIR,
    CONTEXT_WINDOWS,
    DEFAULT_CLAUDE_CONTEXT_WINDOW,
    MAX_EVENT_LOG_ENTRIES,
)
from state import ClaudeState, ClaudeUsage, DashboardState, EventLogEntry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_active_session_file(projects_dir: Path) -> Path | None:
    """
    Walk projects_dir recursively and return the most recently modified
    .jsonl file that was touched within ACTIVE_SESSION_WINDOW_SECONDS.
    Returns None if nothing qualifies.
    """
    now = time.time()
    best: tuple[float, Path] | None = None

    if not projects_dir.exists():
        return None

    for path in projects_dir.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        age = now - mtime
        if age <= ACTIVE_SESSION_WINDOW_SECONDS:
            if best is None or mtime > best[0]:
                best = (mtime, path)

    return best[1] if best else None


def _resolve_context_window(model: str) -> int:
    """Look up context window size from model name prefix."""
    for prefix, size in CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return DEFAULT_CLAUDE_CONTEXT_WINDOW


def _parse_usage_from_line(raw: str) -> ClaudeUsage | None:
    """
    Parse one JSONL line. Returns a ClaudeUsage if the line is an assistant
    message with a usage block; otherwise None.
    """
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None

    msg = entry.get("message", {})
    if msg.get("role") != "assistant":
        return None

    usage = msg.get("usage")
    if usage is None:
        return None

    return ClaudeUsage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
    )


def _extract_model_from_line(raw: str) -> str | None:
    """Return the model name from an assistant message line, if present."""
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return entry.get("message", {}).get("model") or None


def _session_id_from_path(path: Path) -> str:
    """Derive a short session ID from the file stem."""
    stem = path.stem
    return stem[:8] if len(stem) >= 8 else stem


def _full_parse_session(path: Path) -> tuple[ClaudeUsage, ClaudeUsage, str]:
    """
    Read the entire session file.

    Returns:
        (last_usage, cumulative_usage, model_name)

        last_usage      — token counts from the final assistant message.
                          Used for context window fill (each turn includes
                          the full history, so summing inflates the number).
        cumulative_usage — running totals across all turns.
                          output_tokens and cache_creation_input_tokens are
                          meaningful to accumulate (new work done this session).
    """
    last = ClaudeUsage()
    cumulative = ClaudeUsage()
    model = ""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                detected = _extract_model_from_line(raw)
                if detected:
                    model = detected
                usage = _parse_usage_from_line(raw)
                if usage:
                    last = usage
                    cumulative = ClaudeUsage(
                        input_tokens=cumulative.input_tokens + usage.input_tokens,
                        output_tokens=cumulative.output_tokens + usage.output_tokens,
                        cache_read_input_tokens=cumulative.cache_read_input_tokens + usage.cache_read_input_tokens,
                        cache_creation_input_tokens=cumulative.cache_creation_input_tokens + usage.cache_creation_input_tokens,
                    )
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)

    return last, cumulative, model


# ---------------------------------------------------------------------------
# Watchdog handler
# ---------------------------------------------------------------------------


class _ClaudeFileHandler(FileSystemEventHandler):
    """Handles file modification events for Claude session JSONL files."""

    def __init__(self, collector: "ClaudeCollector") -> None:
        super().__init__()
        self._collector = collector

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.suffix != ".jsonl":
            return
        self._collector.on_file_modified(path)


# ---------------------------------------------------------------------------
# Main collector
# ---------------------------------------------------------------------------


class ClaudeCollector:
    """
    Watches ~/.claude/projects for active session JSONL files and updates
    DashboardState with the latest token usage.

    Lifecycle:
        collector = ClaudeCollector(dashboard_state)
        collector.start()   # non-blocking, starts watchdog observer thread
        ...
        collector.stop()
    """

    def __init__(self, state: DashboardState) -> None:
        self._state = state
        self._observer: Observer | None = None
        self._lock = threading.Lock()

        # Currently tracked session file
        self._active_path: Path | None = None
        self._file_offset: int = 0

        # Last single-turn snapshot (for context fill %)
        self._last_usage = ClaudeUsage()
        # Cumulative session totals (for stats panel)
        self._cumulative_usage = ClaudeUsage()
        self._model = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog observer and do an initial session scan."""
        self._initial_scan()

        handler = _ClaudeFileHandler(self)
        self._observer = Observer()
        if CLAUDE_PROJECTS_DIR.exists():
            self._observer.schedule(handler, str(CLAUDE_PROJECTS_DIR), recursive=True)
        self._observer.start()
        logger.info("ClaudeCollector started, watching %s", CLAUDE_PROJECTS_DIR)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
        logger.info("ClaudeCollector stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _initial_scan(self) -> None:
        """Find the active session file and do a full parse from the start."""
        active = _find_active_session_file(CLAUDE_PROJECTS_DIR)
        if active is None:
            self._emit_event("INFO", "No active Claude session found", "claude")
            return

        self._switch_to_session(active, full_parse=True)

    def _switch_to_session(self, path: Path, *, full_parse: bool) -> None:
        """Switch tracking to a new (or re-detected) session file."""
        with self._lock:
            session_id = _session_id_from_path(path)

            last, cumulative, model = _full_parse_session(path)
            self._last_usage = last
            self._cumulative_usage = cumulative
            self._model = model

            # Position offset at end of file for incremental tailing
            try:
                self._file_offset = path.stat().st_size
            except OSError:
                self._file_offset = 0

            self._active_path = path
            self._push_state(session_id)

        self._emit_event(
            "INFO",
            f"Claude session active: {session_id} ({self._model})",
            "claude",
        )

    def on_file_modified(self, path: Path) -> None:
        """Called by the watchdog handler when any .jsonl file changes."""
        with self._lock:
            if self._active_path is None:
                # First modification we've seen — adopt this file
                self._active_path = path
                self._file_offset = 0
                self._last_usage = ClaudeUsage()
                self._cumulative_usage = ClaudeUsage()

            if path != self._active_path:
                # A different file was modified — check if it's newer/active
                try:
                    known_mtime = self._active_path.stat().st_mtime if self._active_path.exists() else 0.0
                    new_mtime = path.stat().st_mtime
                except OSError:
                    return

                if new_mtime > known_mtime:
                    # Switch to the newer session (release lock first)
                    pass  # handled below
                else:
                    return

        # If we need to switch sessions, do it outside the per-file lock
        if path != self._active_path:
            self._switch_to_session(path, full_parse=False)
            return

        self._tail_and_update(path)

    def _tail_and_update(self, path: Path) -> None:
        """Read new bytes from the file since the last offset and parse them."""
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

            changed = False
            for raw in new_content.splitlines():
                raw = raw.strip()
                if not raw:
                    continue

                detected_model = _extract_model_from_line(raw)
                if detected_model:
                    self._model = detected_model

                usage = _parse_usage_from_line(raw)
                if usage:
                    self._last_usage = usage
                    self._cumulative_usage = ClaudeUsage(
                        input_tokens=self._cumulative_usage.input_tokens + usage.input_tokens,
                        output_tokens=self._cumulative_usage.output_tokens + usage.output_tokens,
                        cache_read_input_tokens=self._cumulative_usage.cache_read_input_tokens + usage.cache_read_input_tokens,
                        cache_creation_input_tokens=self._cumulative_usage.cache_creation_input_tokens + usage.cache_creation_input_tokens,
                    )
                    changed = True

            if changed:
                session_id = _session_id_from_path(path)
                self._push_state(session_id)

    def _push_state(self, session_id: str) -> None:
        """Build a new ClaudeState from current snapshots and push it."""
        # Called while self._lock is held
        context_window = _resolve_context_window(self._model)
        new_state = ClaudeState(
            session_id=session_id,
            model=self._model,
            context_window=context_window,
            # Latest single-turn snapshot — drives context fill %
            latest_input_tokens=self._last_usage.input_tokens,
            latest_cache_read_tokens=self._last_usage.cache_read_input_tokens,
            latest_cache_creation_tokens=self._last_usage.cache_creation_input_tokens,
            latest_output_tokens=self._last_usage.output_tokens,
            # Cumulative session totals — drives stats panel
            output_tokens_total=self._cumulative_usage.output_tokens,
            cache_creation_total=self._cumulative_usage.cache_creation_input_tokens,
            session_file=str(self._active_path or ""),
            last_updated=datetime.now(),
        )
        self._state.update_claude(new_state)

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
