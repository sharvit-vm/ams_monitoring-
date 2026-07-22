"""
intake/queue.py

A simple file-backed queue for ErrorEvents.

Design decisions:
- File-backed (queue.json) so events survive restarts
- Deduplication by fingerprint — same bug won't queue twice
- Thread-safe via a file lock
- Swappable to Redis or a proper queue later without changing
  anything in the agents — they just call push() and pop()

Usage:
    from intake.queue import EventQueue

    q = EventQueue()
    q.push(event)           # add to queue
    events = q.pending()    # read all pending
    q.update_status(id, "running")
    q.update_status(id, "done")
"""

import json
import os
import threading
from datetime import datetime
from typing import List, Optional
from .schemas import ErrorEvent


QUEUE_FILE = os.getenv("QUEUE_FILE", "data/event_queue.json")


class EventQueue:
    """
    File-backed queue. All operations are thread-safe.
    The queue is just a JSON list of ErrorEvent dicts on disk.
    """

    def __init__(self, queue_file: str = QUEUE_FILE):
        self.queue_file = queue_file
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(queue_file), exist_ok=True)
        if not os.path.exists(queue_file):
            self._write([])

    # ── Core operations ────────────────────────────────────────

    def push(self, event: ErrorEvent) -> bool:
        """
        Add an event to the queue.

        Returns True if added, False if deduplicated
        (an event with the same fingerprint is already pending or running).
        """
        with self._lock:
            events = self._read()

            # Deduplication — don't queue the same bug twice
            active_fingerprints = {
                e["fingerprint"]
                for e in events
                if e["status"] in ("pending", "running")
            }

            if event.fingerprint in active_fingerprints:
                print(f"[queue] Deduplicated: {event.fingerprint} already active")
                return False

            events.append(event.model_dump(mode="json"))
            self._write(events)
            print(f"[queue] Pushed event {event.id} ({event.error_type})")
            return True

    def pending(self) -> List[ErrorEvent]:
        """Return all events with status=pending."""
        with self._lock:
            events = self._read()
            return [
                ErrorEvent(**e)
                for e in events
                if e["status"] == "pending"
            ]

    def claim_next(self) -> Optional[ErrorEvent]:
        """
        Atomically claim the oldest pending event.

        This lets a durable worker pick work up from the file-backed queue
        without losing events when the web process restarts.
        """
        with self._lock:
            events = self._read()
            for e in events:
                if e.get("status") == "pending":
                    e["status"] = "running"
                    e["updated_at"] = datetime.utcnow().isoformat()
                    self._write(events)
                    return ErrorEvent(**e)
            return None

    def reset_interrupted(self) -> int:
        """
        Move in-flight events back to pending after a process restart.

        Jobs marked rca_done may not have created a PR yet, so they are also
        retried from the beginning. Completed and failed jobs are left alone.
        """
        reset_count = 0
        with self._lock:
            events = self._read()
            for e in events:
                if e.get("status") in ("running", "rca_done"):
                    e["status"] = "pending"
                    e["updated_at"] = datetime.utcnow().isoformat()
                    e["recovered_from_restart"] = True
                    reset_count += 1
            if reset_count:
                self._write(events)
        return reset_count

    def all_events(self) -> List[ErrorEvent]:
        """Return all events regardless of status."""
        with self._lock:
            return [ErrorEvent(**e) for e in self._read()]

    def all_records(self) -> list[dict]:
        """Return raw queue records, including RCA and fix results."""
        with self._lock:
            return self._read()

    def get(self, event_id: str) -> Optional[ErrorEvent]:
        """Fetch a single event by ID."""
        with self._lock:
            for e in self._read():
                if e["id"] == event_id:
                    return ErrorEvent(**e)
            return None

    def get_record(self, event_id: str) -> Optional[dict]:
        """Fetch a raw queue record by ID, including RCA and fix results."""
        with self._lock:
            for e in self._read():
                if e["id"] == event_id:
                    return e
            return None

    def update_status(self, event_id: str, status: str, extra: dict = None):
        """
        Update an event's status and optionally merge in extra fields
        (e.g. storing the fix result once the pipeline completes).
        """
        with self._lock:
            events = self._read()
            for e in events:
                if e["id"] == event_id:
                    e["status"] = status
                    e["updated_at"] = datetime.utcnow().isoformat()
                    if extra:
                        e.update(extra)
                    break
            self._write(events)

    def delete(self, event_id: str):
        """Remove an event from the queue."""
        with self._lock:
            events = [e for e in self._read() if e["id"] != event_id]
            self._write(events)

    def clear(self):
        """Wipe the entire queue. Useful for testing."""
        with self._lock:
            self._write([])

    # ── Stats ──────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return counts by status."""
        events = self._read()
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "total": 0}
        for e in events:
            status = e.get("status", "pending")
            counts[status] = counts.get(status, 0) + 1
            counts["total"] += 1
        return counts

    # ── Internal ───────────────────────────────────────────────

    def _read(self) -> list:
        try:
            with open(self.queue_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, events: list):
        with open(self.queue_file, "w") as f:
            json.dump(events, f, indent=2, default=str)


# ── Module-level singleton ─────────────────────────────────────────────────────
# Import this anywhere: from intake.queue import queue
queue = EventQueue()
