from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any

from events import BaseEvent
from file_lock import locked_file
from secure_storage import (
    append_private_text,
    open_private_text,
    write_private_text_atomic,
)

DEFAULT_QUEUE_ROOT = Path(os.environ.get("PROMTY_QUEUE_DIR", "~/.promty/events")).expanduser()
LEGACY_QUEUE_PATH = Path("~/.promty/events.jsonl").expanduser()


def _safe_path_part(value: Any) -> str:
    safe_value = "".join(char for char in str(value) if char.isalnum() or char in ("-", "_"))
    return safe_value or "unknown"


class JSONLQueue:
    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.environ.get("PROMTY_QUEUE_PATH")
        self.root = None if configured_path else DEFAULT_QUEUE_ROOT
        self.path = Path(configured_path).expanduser() if configured_path else DEFAULT_QUEUE_ROOT
        self.lock_path = (
            self.path.with_suffix(f"{self.path.suffix}.lock")
            if self.root is None
            else self.path / ".lock"
        )

    def push(self, event: BaseEvent) -> None:
        with self._locked():
            queue_file = self._event_path(event)
            append_private_text(
                queue_file,
                json.dumps(event.to_dict(), ensure_ascii=False) + "\n",
            )

    def read_batch(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._locked():
            events: list[dict[str, Any]] = []
            for queue_file in self._queue_files():
                with open_private_text(queue_file, "r") as file:
                    for line in file:
                        if len(events) >= limit:
                            return events
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, dict):
                            events.append(event)
            return events

    def ack(self, event_ids: set[str]) -> None:
        if not event_ids:
            return

        with self._locked():
            for queue_file in self._queue_files():
                self._ack_file(queue_file, event_ids)

    @property
    def conflict_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.conflicts.jsonl")

    def quarantine(self, events: list[dict[str, Any]], *, reason: str) -> Path:
        """Preserve permanently rejected events outside the active retry queue."""

        valid_events = [
            event
            for event in events
            if isinstance((event_id := event.get("id")), str) and event_id
        ]
        event_ids = {str(event["id"]) for event in valid_events}
        if not event_ids:
            raise ValueError("Cannot quarantine events without an id")

        conflict_path = self.conflict_path
        records = "".join(
            json.dumps(
                {
                    "conflict": {"reason": reason},
                    "event": event,
                },
                ensure_ascii=False,
            )
            + "\n"
            for event in valid_events
        )
        with self._locked():
            # Preserve first, then remove from the active queue. A crash can create
            # a duplicate conflict record but cannot silently lose the event.
            append_private_text(conflict_path, records)
            for queue_file in self._queue_files():
                self._ack_file(queue_file, event_ids)
        return conflict_path

    def _event_path(self, event: BaseEvent) -> Path:
        if self.root is None:
            return self.path
        return (
            self.root
            / _safe_path_part(event.project_id)
            / _safe_path_part(event.session_id)
            / "events.jsonl"
        )

    def _queue_files(self) -> list[Path]:
        if self.root is None:
            return [self.path] if self.path.exists() else []

        queue_files: list[Path] = []
        if LEGACY_QUEUE_PATH.exists():
            queue_files.append(LEGACY_QUEUE_PATH)
        if self.root.exists():
            queue_files.extend(sorted(self.root.glob("*/*/events.jsonl")))
        return queue_files

    def _ack_file(self, queue_file: Path, event_ids: set[str]) -> None:
        if not queue_file.exists():
            return

        remaining: list[str] = []
        with open_private_text(queue_file, "r") as file:
            for line in file:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("id") not in event_ids:
                    remaining.append(line)

        if remaining:
            write_private_text_atomic(queue_file, "".join(remaining))
            return

        try:
            queue_file.unlink()
        except OSError:
            pass

    @contextmanager
    def _locked(self):
        with locked_file(self.lock_path):
            yield
