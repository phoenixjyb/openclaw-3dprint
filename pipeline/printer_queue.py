"""Cross-process printer queue — ensures only one job prints at a time.

Within a single process, uses asyncio.Lock to serialize print jobs.
Across processes, uses fcntl.flock on a shared lock file.

Jobs can run through LLM, mesh generation, and slicing in parallel —
only the actual print stage is serialized.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

LOCK_FILE = Path("/tmp/openclaw-3dprint-printer.lock")
QUEUE_FILE = Path("/tmp/openclaw-3dprint-queue.json")


class PrinterQueue:
    """Manages printer access across processes."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._waiters: deque[str] = deque()
        self._active_job: str | None = None
        self._lock_fd: int | None = None

    @property
    def queue_position(self) -> int:
        return len(self._waiters)

    @property
    def active_job_id(self) -> str | None:
        return self._active_job

    def _acquire_file_lock(self) -> None:
        """Acquire cross-process file lock (blocking in thread)."""
        LOCK_FILE.touch(exist_ok=True)
        self._lock_fd = open(LOCK_FILE, "w")  # noqa: SIM115
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        self._lock_fd.write(f"{self._active_job}\n")
        self._lock_fd.flush()

    def _release_file_lock(self) -> None:
        """Release cross-process file lock."""
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            except OSError:
                pass
            self._lock_fd = None

    async def acquire(self, job_id: str) -> int:
        """Acquire the printer for a job. Returns wait position (0 = immediate)."""
        self._waiters.append(job_id)
        position = len(self._waiters)
        log.info("Job %s queued at position %d", job_id, position)

        await self._lock.acquire()

        self._active_job = job_id
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._acquire_file_lock)

        if job_id in self._waiters:
            self._waiters.remove(job_id)

        log.info("Job %s acquired printer lock", job_id)
        return 0

    def release(self, job_id: str) -> None:
        """Release the printer after a job completes or fails."""
        if self._active_job == job_id:
            self._release_file_lock()
            self._active_job = None
            if self._lock.locked():
                self._lock.release()
            log.info("Job %s released printer lock", job_id)
        else:
            if job_id in self._waiters:
                self._waiters.remove(job_id)
                log.info("Job %s removed from queue", job_id)

    def status(self) -> dict:
        """Return queue status for API responses."""
        return {
            "active_job": self._active_job,
            "queue_length": len(self._waiters),
            "waiting_jobs": list(self._waiters),
        }


# Shared singleton
_queue: PrinterQueue | None = None


def get_printer_queue() -> PrinterQueue:
    global _queue
    if _queue is None:
        _queue = PrinterQueue()
    return _queue
