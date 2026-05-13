# Kiro Gateway - Request Queue Type Definitions
# Copyright (C) 2024 Kiro Gateway Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Request queue type definitions for Kiro Gateway.

Provides dataclasses used by the request queue system to track
queued requests, queue-level metrics, and per-account concurrency slots.

Classes:
    QueuedRequest: A single request waiting in the queue.
    QueueStats: Aggregate metrics for the queue.
    AccountSlot: Per-account concurrency tracking.
    ConcurrencyLimiter: Per-account semaphore-based concurrency limiter.
    FairScheduler: Round-robin fair scheduler across clients.
    RequestQueueOrchestrator: Unified entry point combining limiter + scheduler.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from collections import deque
from typing import Any, Awaitable, Callable, Optional, Dict, Deque, AsyncGenerator

from loguru import logger

from kiro.exceptions import QueueFullError, QueueTimeoutError
from kiro.queue_config import MAX_CONCURRENT_PER_ACCOUNT, QUEUE_MAX_SIZE, QUEUE_TIMEOUT_SECONDS


@dataclass
class QueuedRequest:
    """
    Represents a request waiting in the queue.

    Attributes:
        request_id: Unique identifier for this request.
        client_id: Identifies the originating client (IP or API key hash).
        model: Requested model name.
        enqueued_at: Unix timestamp (time.time()) when the request was enqueued.
        priority: Reserved for future priority scheduling; all requests are
            equal for now (default 0).
    """

    request_id: str
    client_id: str
    model: str
    enqueued_at: float
    priority: int = 0


@dataclass
class QueueStats:
    """
    Aggregate metrics for the request queue.

    Attributes:
        pending: Number of requests currently waiting in the queue.
        active: Number of requests currently being processed.
        completed: Total number of requests that have finished processing.
        rejected: Total number of requests rejected because the queue was full.
        avg_wait_time: Rolling average time (seconds) requests spend in the
            queue before processing begins.
    """

    pending: int
    active: int
    completed: int
    rejected: int
    avg_wait_time: float


@dataclass
class AccountSlot:
    """
    Per-account concurrency tracking slot.

    Attributes:
        account_id: Unique identifier for the account.
        max_concurrent: Maximum number of requests allowed to run
            concurrently on this account.
        active_requests: Current number of in-flight requests on this
            account (default 0).
    """

    account_id: str
    max_concurrent: int
    active_requests: int = 0


class FairScheduler:
    """
    Bounded request queue with round-robin fair scheduling across clients.

    Ensures no single client can starve others by rotating through client_ids
    when dequeuing requests. Each client maintains its own FIFO queue, and
    dequeue cycles through clients in insertion order.

    Args:
        max_size: Maximum total pending requests before rejecting new ones.
    """

    def __init__(self, max_size: int = QUEUE_MAX_SIZE) -> None:
        self._max_size: int = max_size
        self._client_queues: Dict[str, Deque[QueuedRequest]] = {}
        self._rotation: Deque[str] = deque()
        self._total_size: int = 0
        self._completed: int = 0
        self._rejected: int = 0

    def enqueue(self, request: QueuedRequest) -> bool:
        """
        Add a request to the queue.

        Places the request in the appropriate client's FIFO queue. If the client
        is new, adds them to the rotation order.

        Args:
            request: The queued request to add.

        Returns:
            True if the request was accepted, False if the queue is full.
        """
        if self._total_size >= self._max_size:
            self._rejected += 1
            return False

        client_id = request.client_id
        if client_id not in self._client_queues:
            self._client_queues[client_id] = deque()
            self._rotation.append(client_id)

        self._client_queues[client_id].append(request)
        self._total_size += 1
        return True

    def dequeue(self) -> Optional[QueuedRequest]:
        """
        Remove and return the next request using round-robin scheduling.

        Takes from the front of the rotation, pops from that client's queue,
        then moves the client to the back of the rotation. If a client's queue
        is empty after popping, removes them from rotation entirely.

        Returns:
            The next QueuedRequest, or None if the queue is empty.
        """
        if not self._rotation:
            return None

        client_id = self._rotation.popleft()
        client_deque = self._client_queues[client_id]
        request = client_deque.popleft()
        self._total_size -= 1
        self._completed += 1

        if client_deque:
            self._rotation.append(client_id)
        else:
            del self._client_queues[client_id]

        return request

    def get_stats(self) -> QueueStats:
        """
        Return aggregate queue metrics.

        Returns:
            QueueStats with current pending count, completed/rejected counters.
            Active and avg_wait_time are always 0 (tracked externally).
        """
        return QueueStats(
            pending=self._total_size,
            active=0,
            completed=self._completed,
            rejected=self._rejected,
            avg_wait_time=0.0,
        )

    def size(self) -> int:
        """
        Return the total number of pending requests across all clients.

        Returns:
            Total pending request count.
        """
        return self._total_size

    def is_full(self) -> bool:
        """
        Check whether the queue has reached its maximum capacity.

        Returns:
            True if size >= max_size, False otherwise.
        """
        return self._total_size >= self._max_size


class ConcurrencyLimiter:
    """
    Per-account concurrency limiter using asyncio.Semaphore.

    Manages a pool of semaphores (one per account) to enforce maximum
    concurrent request limits. Semaphores are created lazily on first access.

    Args:
        max_concurrent: Maximum simultaneous requests per account.
            Defaults to MAX_CONCURRENT_PER_ACCOUNT from queue_config.
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_PER_ACCOUNT) -> None:
        self._max_concurrent: int = max_concurrent
        self._semaphores: Dict[str, asyncio.Semaphore] = {}

    def _get_semaphore(self, account_id: str) -> asyncio.Semaphore:
        """
        Get or create the semaphore for a given account.

        Semaphores are created lazily on first access with the configured
        max_concurrent value.

        Args:
            account_id: Unique identifier for the account.

        Returns:
            The asyncio.Semaphore for this account.
        """
        if account_id not in self._semaphores:
            self._semaphores[account_id] = asyncio.Semaphore(self._max_concurrent)
            logger.debug(
                f"Created semaphore for account '{account_id}' "
                f"with {self._max_concurrent} slots"
            )
        return self._semaphores[account_id]

    async def acquire(self, account_id: str, timeout: float = 30.0) -> bool:
        """
        Acquire a concurrency slot for the given account.

        Blocks until a slot is available or the timeout expires.

        Args:
            account_id: Unique identifier for the account.
            timeout: Maximum seconds to wait for a slot. Defaults to 30.0.

        Returns:
            True if a slot was acquired, False if the timeout expired.
        """
        semaphore = self._get_semaphore(account_id)
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
            logger.debug(
                f"Acquired slot for account '{account_id}', "
                f"remaining: {semaphore._value}"
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout acquiring slot for account '{account_id}' "
                f"after {timeout}s"
            )
            return False

    def release(self, account_id: str) -> None:
        """
        Release a concurrency slot for the given account.

        No-op with a warning if the account has no semaphore (never acquired).

        Args:
            account_id: Unique identifier for the account.
        """
        if account_id not in self._semaphores:
            logger.warning(
                f"Attempted to release slot for unknown account '{account_id}'"
            )
            return
        semaphore = self._semaphores[account_id]
        semaphore.release()
        logger.debug(
            f"Released slot for account '{account_id}', "
            f"available: {semaphore._value}"
        )

    def get_available_slots(self, account_id: str) -> int:
        """
        Get the number of available concurrency slots for an account.

        Args:
            account_id: Unique identifier for the account.

        Returns:
            Number of available slots. Returns max_concurrent if the account
            has never been accessed (no semaphore created yet).
        """
        if account_id not in self._semaphores:
            return self._max_concurrent
        return self._semaphores[account_id]._value

    def is_available(self, account_id: str) -> bool:
        """
        Check if at least one concurrency slot is available for an account.

        Args:
            account_id: Unique identifier for the account.

        Returns:
            True if at least one slot is available, False otherwise.
        """
        return self.get_available_slots(account_id) > 0

    @asynccontextmanager
    async def slot(
        self, account_id: str, timeout: float = 30.0
    ) -> AsyncGenerator[None, None]:
        """
        Async context manager that acquires and releases a concurrency slot.

        Acquires a slot on entry and guarantees release on exit, even if
        an exception occurs within the block.

        Args:
            account_id: Unique identifier for the account.
            timeout: Maximum seconds to wait for a slot. Defaults to 30.0.

        Raises:
            asyncio.TimeoutError: If the slot could not be acquired within
                the timeout period.

        Yields:
            None once the slot is acquired.
        """
        acquired = await self.acquire(account_id, timeout=timeout)
        if not acquired:
            raise asyncio.TimeoutError(
                f"Could not acquire slot for account '{account_id}' "
                f"within {timeout}s"
            )
        try:
            yield
        finally:
            self.release(account_id)


class RequestQueueOrchestrator:
    """
    Unified request scheduling system combining ConcurrencyLimiter and FairScheduler.

    Provides a single entry point (submit) that enqueues requests, waits for a
    turn via fair scheduling, acquires a concurrency slot, executes the request,
    and releases the slot. A background worker continuously dequeues requests
    and signals them to proceed.

    Args:
        concurrency_limiter: Per-account concurrency limiter instance.
        fair_scheduler: Fair round-robin scheduler instance.
        queue_timeout: Max seconds a request may wait in queue before timeout.
            Defaults to QUEUE_TIMEOUT_SECONDS from queue_config.
    """

    def __init__(
        self,
        concurrency_limiter: ConcurrencyLimiter,
        fair_scheduler: FairScheduler,
        queue_timeout: float = QUEUE_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initialize RequestQueueOrchestrator.

        Args:
            concurrency_limiter: Per-account concurrency limiter instance.
            fair_scheduler: Fair round-robin scheduler instance.
            queue_timeout: Max seconds a request may wait in queue.
        """
        self._limiter = concurrency_limiter
        self._scheduler = fair_scheduler
        self._queue_timeout = queue_timeout
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._pending_events: Dict[str, asyncio.Event] = {}
        self._stats = QueueStats(
            pending=0, active=0, completed=0, rejected=0, avg_wait_time=0.0
        )
        self._total_wait_time: float = 0.0
        self._notify_event = asyncio.Event()
        # Tracks requests that entered submit() but haven't started execute_fn yet.
        # Used for capacity checks since the worker drains the scheduler immediately.
        self._waiting_count: int = 0

    async def submit(
        self,
        client_id: str,
        model: str,
        account_id: str,
        execute_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """
        Main API: enqueue request, wait for turn, acquire slot, execute, release.

        Submits a request to the queue system. The request waits for its turn
        (fair scheduling), acquires a concurrency slot for the account, then
        executes the provided callable.

        Args:
            client_id: Identifies the originating client (IP or API key hash).
            model: Requested model name.
            account_id: Account to acquire concurrency slot for.
            execute_fn: Async callable to execute when the request's turn arrives.

        Returns:
            The result of execute_fn().

        Raises:
            QueueFullError: If the queue is at capacity and cannot accept the request.
            QueueTimeoutError: If the request waited longer than queue_timeout.
        """
        # 1. Check if queue is full (use waiting_count since worker drains scheduler fast)
        if self._waiting_count >= self._scheduler._max_size:
            self._stats.rejected += 1
            raise QueueFullError(
                queue_size=self._waiting_count,
                max_size=self._scheduler._max_size,
            )

        # 2. Create QueuedRequest and enqueue
        request_id = uuid.uuid4().hex
        enqueued_at = time.time()
        queued_request = QueuedRequest(
            request_id=request_id,
            client_id=client_id,
            model=model,
            enqueued_at=enqueued_at,
        )

        accepted = self._scheduler.enqueue(queued_request)
        if not accepted:
            self._stats.rejected += 1
            raise QueueFullError(
                queue_size=self._scheduler.size(),
                max_size=self._scheduler._max_size,
            )

        # 3. Create Event for this request's turn
        turn_event = asyncio.Event()
        self._pending_events[request_id] = turn_event
        self._stats.pending += 1
        self._waiting_count += 1

        # Notify worker there's work to do
        self._notify_event.set()

        # 4. Wait for turn with timeout
        try:
            await asyncio.wait_for(turn_event.wait(), timeout=self._queue_timeout)
        except asyncio.TimeoutError:
            # Remove from pending events
            self._pending_events.pop(request_id, None)
            self._stats.pending -= 1
            self._waiting_count -= 1
            wait_time = time.time() - enqueued_at
            logger.warning(
                f"Request '{request_id}' timed out after {wait_time:.1f}s "
                f"(client={client_id}, model={model})"
            )
            raise QueueTimeoutError(wait_time=wait_time, request_id=request_id)

        # 5. Acquire concurrency slot

        acquired = await self._limiter.acquire(account_id, timeout=self._queue_timeout)
        if not acquired:
            self._waiting_count -= 1
            self._stats.pending -= 1
            wait_time_total = time.time() - enqueued_at
            raise QueueTimeoutError(
                wait_time=wait_time_total, request_id=request_id
            )

        # Slot acquired — now truly active
        self._waiting_count -= 1
        self._stats.pending -= 1
        wait_time = time.time() - enqueued_at
        self._total_wait_time += wait_time
        self._stats.active += 1

        if self._stats.completed + self._stats.active > 0:
            self._stats.avg_wait_time = self._total_wait_time / (
                self._stats.completed + self._stats.active
            )

        logger.debug(
            f"Request '{request_id}' acquired turn after {wait_time:.2f}s "
            f"(client={client_id}, account={account_id})"
        )

        # 6-9. Execute with slot, release on completion/error
        try:
            result = await execute_fn()
            self._stats.active -= 1
            self._stats.completed += 1
            logger.debug(
                f"Request '{request_id}' completed successfully "
                f"(client={client_id}, account={account_id})"
            )
            return result
        except Exception:
            self._stats.active -= 1
            self._stats.completed += 1
            raise
        finally:
            self._limiter.release(account_id)

    async def start(self) -> None:
        """
        Start the background worker that processes the queue.

        The worker continuously dequeues requests from the FairScheduler
        and signals their corresponding Events to proceed.
        """
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("RequestQueueOrchestrator started")

    async def stop(self) -> None:
        """
        Graceful shutdown: stop accepting new requests and drain active ones.

        Cancels the background worker task and waits for it to finish.
        Active requests are allowed to complete naturally.
        """
        if not self._running:
            return
        self._running = False
        self._notify_event.set()  # Wake up worker so it can exit

        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        logger.info("RequestQueueOrchestrator stopped")

    def get_stats(self) -> QueueStats:
        """
        Return current queue statistics.

        Returns:
            QueueStats with pending, active, completed, rejected, avg_wait_time.
        """
        return QueueStats(
            pending=self._stats.pending,
            active=self._stats.active,
            completed=self._stats.completed,
            rejected=self._stats.rejected,
            avg_wait_time=self._stats.avg_wait_time,
        )

    async def _worker_loop(self) -> None:
        """
        Background worker that dequeues requests and signals their Events.

        Runs continuously while self._running is True. When a request is
        dequeued, its corresponding Event is set, allowing the submit()
        coroutine to proceed with slot acquisition and execution.
        """
        logger.debug("Queue worker loop started")
        while self._running:
            # Try to dequeue a request
            request = self._scheduler.dequeue()
            if request is not None:
                event = self._pending_events.pop(request.request_id, None)
                if event is not None:
                    event.set()
                # Continue immediately to check for more work
                continue

            # No work available, wait for notification
            self._notify_event.clear()
            try:
                await asyncio.wait_for(self._notify_event.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
