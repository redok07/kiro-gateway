# Kiro Gateway - Request Queue Type Definitions Tests
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

"""Tests for request queue type definitions and ConcurrencyLimiter."""

import asyncio

import pytest

from kiro.request_queue import (
    AccountSlot,
    ConcurrencyLimiter,
    FairScheduler,
    QueuedRequest,
    QueueStats,
)


class TestQueuedRequest:
    """Tests for QueuedRequest dataclass."""

    def test_creation_with_required_fields(self):
        """QueuedRequest can be created with all required fields."""
        qr = QueuedRequest(
            request_id="r1",
            client_id="c1",
            model="claude-opus-4.6",
            enqueued_at=1000.0,
        )
        assert qr.request_id == "r1"
        assert qr.client_id == "c1"
        assert qr.model == "claude-opus-4.6"
        assert qr.enqueued_at == 1000.0

    def test_default_priority_is_zero(self):
        """QueuedRequest.priority defaults to 0."""
        qr = QueuedRequest(request_id="r1", client_id="c1", model="m", enqueued_at=1.0)
        assert qr.priority == 0

    def test_explicit_priority(self):
        """QueuedRequest.priority can be set explicitly."""
        qr = QueuedRequest(
            request_id="r2", client_id="c2", model="m", enqueued_at=2.0, priority=5
        )
        assert qr.priority == 5

    def test_fields_are_mutable(self):
        """QueuedRequest fields can be updated after creation."""
        qr = QueuedRequest(request_id="r1", client_id="c1", model="m", enqueued_at=1.0)
        qr.priority = 3
        assert qr.priority == 3

    def test_different_instances_are_independent(self):
        """Two QueuedRequest instances do not share state."""
        qr1 = QueuedRequest(request_id="r1", client_id="c1", model="m1", enqueued_at=1.0)
        qr2 = QueuedRequest(request_id="r2", client_id="c2", model="m2", enqueued_at=2.0)
        assert qr1.request_id != qr2.request_id
        assert qr1.model != qr2.model


class TestQueueStats:
    """Tests for QueueStats dataclass."""

    def test_creation_with_all_fields(self):
        """QueueStats can be created with explicit values."""
        qs = QueueStats(
            pending=5,
            active=2,
            completed=100,
            rejected=3,
            avg_wait_time=1.5,
        )
        assert qs.pending == 5
        assert qs.active == 2
        assert qs.completed == 100
        assert qs.rejected == 3
        assert qs.avg_wait_time == 1.5

    def test_zero_defaults(self):
        """QueueStats fields default to zero."""
        qs = QueueStats(pending=0, active=0, completed=0, rejected=0, avg_wait_time=0.0)
        assert qs.pending == 0
        assert qs.active == 0
        assert qs.completed == 0
        assert qs.rejected == 0
        assert qs.avg_wait_time == 0.0

    def test_fields_are_mutable(self):
        """QueueStats fields can be incremented."""
        qs = QueueStats(pending=0, active=0, completed=0, rejected=0, avg_wait_time=0.0)
        qs.completed += 1
        assert qs.completed == 1


class TestAccountSlot:
    """Tests for AccountSlot dataclass."""

    def test_creation_with_required_fields(self):
        """AccountSlot can be created with required fields."""
        slot = AccountSlot(account_id="acc-1", max_concurrent=5)
        assert slot.account_id == "acc-1"
        assert slot.max_concurrent == 5

    def test_default_active_requests_is_zero(self):
        """AccountSlot.active_requests defaults to 0."""
        slot = AccountSlot(account_id="acc-1", max_concurrent=3)
        assert slot.active_requests == 0

    def test_explicit_active_requests(self):
        """AccountSlot.active_requests can be set explicitly."""
        slot = AccountSlot(account_id="acc-1", max_concurrent=3, active_requests=2)
        assert slot.active_requests == 2

    def test_active_requests_mutable(self):
        """AccountSlot.active_requests can be incremented/decremented."""
        slot = AccountSlot(account_id="acc-1", max_concurrent=3)
        slot.active_requests += 1
        assert slot.active_requests == 1
        slot.active_requests -= 1
        assert slot.active_requests == 0

    def test_different_slots_independent(self):
        """Two AccountSlot instances do not share active_requests state."""
        s1 = AccountSlot(account_id="a1", max_concurrent=2)
        s2 = AccountSlot(account_id="a2", max_concurrent=4)
        s1.active_requests = 1
        assert s2.active_requests == 0


class TestConcurrencyLimiterAcquireRelease:
    """Tests for ConcurrencyLimiter acquire/release cycle."""

    @pytest.mark.asyncio
    async def test_acquire_returns_true(self):
        """acquire() returns True when a slot is available."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        result = await limiter.acquire("acc1")
        assert result is True

    @pytest.mark.asyncio
    async def test_release_restores_slot(self):
        """release() restores the slot after acquire."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        await limiter.acquire("acc1")
        assert limiter.get_available_slots("acc1") == 1
        limiter.release("acc1")
        assert limiter.get_available_slots("acc1") == 2

    @pytest.mark.asyncio
    async def test_acquire_multiple_slots(self):
        """Multiple acquires consume multiple slots."""
        limiter = ConcurrencyLimiter(max_concurrent=3)
        await limiter.acquire("acc1")
        await limiter.acquire("acc1")
        assert limiter.get_available_slots("acc1") == 1

    @pytest.mark.asyncio
    async def test_release_unknown_account_is_noop(self):
        """release() on unknown account does not raise."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        # Should not raise, just log warning
        limiter.release("nonexistent")


class TestConcurrencyLimiterBlocking:
    """Tests for ConcurrencyLimiter blocking behavior."""

    @pytest.mark.asyncio
    async def test_blocks_when_all_slots_taken(self):
        """acquire() returns False (timeout) when all slots are taken."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        await limiter.acquire("acc1")
        await limiter.acquire("acc1")
        # Third acquire should timeout
        result = await limiter.acquire("acc1", timeout=0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_unblocks_after_release(self):
        """acquire() succeeds after a slot is released."""
        limiter = ConcurrencyLimiter(max_concurrent=1)
        await limiter.acquire("acc1")

        async def release_later():
            await asyncio.sleep(0.05)
            limiter.release("acc1")

        asyncio.create_task(release_later())
        result = await limiter.acquire("acc1", timeout=1.0)
        assert result is True


class TestConcurrencyLimiterSlots:
    """Tests for get_available_slots and is_available."""

    @pytest.mark.asyncio
    async def test_get_available_slots_no_semaphore(self):
        """get_available_slots returns max_concurrent for unknown account."""
        limiter = ConcurrencyLimiter(max_concurrent=5)
        assert limiter.get_available_slots("new_acc") == 5

    @pytest.mark.asyncio
    async def test_get_available_slots_decreases_after_acquire(self):
        """get_available_slots decreases by 1 after each acquire."""
        limiter = ConcurrencyLimiter(max_concurrent=3)
        assert limiter.get_available_slots("acc1") == 3
        await limiter.acquire("acc1")
        assert limiter.get_available_slots("acc1") == 2
        await limiter.acquire("acc1")
        assert limiter.get_available_slots("acc1") == 1

    @pytest.mark.asyncio
    async def test_is_available_true_when_slots_exist(self):
        """is_available returns True when slots are available."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        assert limiter.is_available("acc1") is True

    @pytest.mark.asyncio
    async def test_is_available_false_when_all_taken(self):
        """is_available returns False when all slots are taken."""
        limiter = ConcurrencyLimiter(max_concurrent=1)
        await limiter.acquire("acc1")
        assert limiter.is_available("acc1") is False

    @pytest.mark.asyncio
    async def test_is_available_unknown_account(self):
        """is_available returns True for account with no semaphore."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        assert limiter.is_available("never_seen") is True


class TestConcurrencyLimiterLazyCreation:
    """Tests for lazy semaphore creation."""

    @pytest.mark.asyncio
    async def test_semaphore_not_created_until_access(self):
        """Semaphore is not created until first acquire."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        assert len(limiter._semaphores) == 0

    @pytest.mark.asyncio
    async def test_semaphore_created_on_first_acquire(self):
        """Semaphore is created on first acquire call."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        await limiter.acquire("acc1")
        assert "acc1" in limiter._semaphores

    @pytest.mark.asyncio
    async def test_get_available_slots_does_not_create_semaphore(self):
        """get_available_slots does not create a semaphore for unknown account."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        limiter.get_available_slots("acc1")
        assert "acc1" not in limiter._semaphores


class TestConcurrencyLimiterContextManager:
    """Tests for the slot() async context manager."""

    @pytest.mark.asyncio
    async def test_slot_acquires_and_releases(self):
        """slot() acquires on enter and releases on exit."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        async with limiter.slot("acc1"):
            assert limiter.get_available_slots("acc1") == 1
        assert limiter.get_available_slots("acc1") == 2

    @pytest.mark.asyncio
    async def test_slot_releases_on_exception(self):
        """slot() releases the slot even when an exception occurs."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        with pytest.raises(ValueError, match="test error"):
            async with limiter.slot("acc1"):
                assert limiter.get_available_slots("acc1") == 1
                raise ValueError("test error")
        # Slot must be released despite exception
        assert limiter.get_available_slots("acc1") == 2

    @pytest.mark.asyncio
    async def test_slot_raises_timeout_error(self):
        """slot() raises asyncio.TimeoutError when acquire times out."""
        limiter = ConcurrencyLimiter(max_concurrent=1)
        await limiter.acquire("acc1")
        with pytest.raises(asyncio.TimeoutError):
            async with limiter.slot("acc1", timeout=0.1):
                pass  # pragma: no cover

    @pytest.mark.asyncio
    async def test_slot_nested_usage(self):
        """Multiple slot() calls consume multiple slots."""
        limiter = ConcurrencyLimiter(max_concurrent=3)
        async with limiter.slot("acc1"):
            assert limiter.get_available_slots("acc1") == 2
            async with limiter.slot("acc1"):
                assert limiter.get_available_slots("acc1") == 1
            assert limiter.get_available_slots("acc1") == 2
        assert limiter.get_available_slots("acc1") == 3


class TestConcurrencyLimiterMultipleAccounts:
    """Tests for independence between accounts."""

    @pytest.mark.asyncio
    async def test_accounts_are_independent(self):
        """Acquiring slots on one account does not affect another."""
        limiter = ConcurrencyLimiter(max_concurrent=1)
        await limiter.acquire("acc1")
        # acc2 should still have full capacity
        assert limiter.is_available("acc2") is True
        result = await limiter.acquire("acc2")
        assert result is True

    @pytest.mark.asyncio
    async def test_release_one_account_does_not_affect_other(self):
        """Releasing a slot on one account does not change another."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        await limiter.acquire("acc1")
        await limiter.acquire("acc2")
        limiter.release("acc1")
        assert limiter.get_available_slots("acc1") == 2
        assert limiter.get_available_slots("acc2") == 1

    @pytest.mark.asyncio
    async def test_many_accounts_coexist(self):
        """Multiple accounts can each have their own semaphore."""
        limiter = ConcurrencyLimiter(max_concurrent=2)
        for i in range(10):
            await limiter.acquire(f"acc{i}")
        assert len(limiter._semaphores) == 10
        for i in range(10):
            assert limiter.get_available_slots(f"acc{i}") == 1


# ==============================================================================
# FairScheduler Tests
# ==============================================================================


def _make_request(request_id: str = "r1", client_id: str = "c1", model: str = "m") -> QueuedRequest:
    """Helper to create a QueuedRequest with minimal boilerplate."""
    return QueuedRequest(request_id=request_id, client_id=client_id, model=model, enqueued_at=1.0)


class TestFairSchedulerEnqueue:
    """Tests for FairScheduler.enqueue()."""

    def test_enqueue_single_request_returns_true(self):
        """Enqueuing a request to a non-full queue returns True."""
        scheduler = FairScheduler(max_size=10)
        result = scheduler.enqueue(_make_request("r1", "A"))
        assert result is True

    def test_enqueue_increases_size(self):
        """Enqueuing a request increases the queue size by 1."""
        scheduler = FairScheduler(max_size=10)
        assert scheduler.size() == 0
        scheduler.enqueue(_make_request("r1", "A"))
        assert scheduler.size() == 1

    def test_enqueue_when_full_returns_false(self):
        """Enqueuing when queue is at max_size returns False."""
        scheduler = FairScheduler(max_size=3)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        scheduler.enqueue(_make_request("r3", "A"))
        result = scheduler.enqueue(_make_request("r4", "A"))
        assert result is False
        assert scheduler.size() == 3

    def test_enqueue_rejected_increments_rejected_counter(self):
        """Rejected enqueue increments the rejected counter in stats."""
        scheduler = FairScheduler(max_size=1)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))  # rejected
        stats = scheduler.get_stats()
        assert stats.rejected == 1

    def test_enqueue_multiple_clients(self):
        """Enqueuing from multiple clients tracks them separately."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "B"))
        scheduler.enqueue(_make_request("r3", "C"))
        assert scheduler.size() == 3

    def test_enqueue_at_boundary_max_size_one(self):
        """Queue with max_size=1 accepts exactly one request."""
        scheduler = FairScheduler(max_size=1)
        assert scheduler.enqueue(_make_request("r1", "A")) is True
        assert scheduler.enqueue(_make_request("r2", "A")) is False


class TestFairSchedulerDequeue:
    """Tests for FairScheduler.dequeue()."""

    def test_dequeue_empty_returns_none(self):
        """Dequeuing from an empty queue returns None."""
        scheduler = FairScheduler(max_size=10)
        assert scheduler.dequeue() is None

    def test_dequeue_single_request(self):
        """Dequeuing returns the only enqueued request."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        result = scheduler.dequeue()
        assert result is not None
        assert result.request_id == "r1"
        assert result.client_id == "A"

    def test_dequeue_decreases_size(self):
        """Dequeuing decreases the queue size by 1."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        assert scheduler.size() == 2
        scheduler.dequeue()
        assert scheduler.size() == 1

    def test_dequeue_increments_completed_counter(self):
        """Each successful dequeue increments the completed counter."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        scheduler.dequeue()
        scheduler.dequeue()
        stats = scheduler.get_stats()
        assert stats.completed == 2

    def test_dequeue_fifo_within_single_client(self):
        """Requests from the same client are dequeued in FIFO order."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        scheduler.enqueue(_make_request("r3", "A"))
        assert scheduler.dequeue().request_id == "r1"
        assert scheduler.dequeue().request_id == "r2"
        assert scheduler.dequeue().request_id == "r3"

    def test_dequeue_after_empty_returns_none(self):
        """After all requests are dequeued, dequeue returns None."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.dequeue()
        assert scheduler.dequeue() is None


class TestFairSchedulerRoundRobin:
    """Tests for fair round-robin scheduling across clients."""

    def test_fair_round_robin_two_clients(self):
        """Two clients with 3 requests each → dequeue alternates A,B,A,B,A,B."""
        scheduler = FairScheduler(max_size=10)
        for i in range(3):
            scheduler.enqueue(QueuedRequest(
                request_id=f"a{i}", client_id="A", model="m", enqueued_at=float(i)
            ))
        for i in range(3):
            scheduler.enqueue(QueuedRequest(
                request_id=f"b{i}", client_id="B", model="m", enqueued_at=float(i)
            ))

        results = [scheduler.dequeue().client_id for _ in range(6)]
        assert results == ["A", "B", "A", "B", "A", "B"]

    def test_fair_round_robin_three_clients(self):
        """Three clients with 2 requests each → dequeue cycles A,B,C,A,B,C."""
        scheduler = FairScheduler(max_size=10)
        for i in range(2):
            scheduler.enqueue(_make_request(f"a{i}", "A"))
        for i in range(2):
            scheduler.enqueue(_make_request(f"b{i}", "B"))
        for i in range(2):
            scheduler.enqueue(_make_request(f"c{i}", "C"))

        results = [scheduler.dequeue().client_id for _ in range(6)]
        assert results == ["A", "B", "C", "A", "B", "C"]

    def test_client_removed_from_rotation_when_empty(self):
        """Client is removed from rotation when their queue empties."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("a0", "A"))
        scheduler.enqueue(_make_request("b0", "B"))
        scheduler.enqueue(_make_request("b1", "B"))

        # A has 1, B has 2. Dequeue: A (A now empty), B, B
        assert scheduler.dequeue().client_id == "A"
        assert scheduler.dequeue().client_id == "B"
        assert scheduler.dequeue().client_id == "B"
        assert scheduler.dequeue() is None

    def test_new_client_gets_slot_in_rotation(self):
        """A new client added after initial enqueue gets a rotation slot."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("a0", "A"))
        scheduler.enqueue(_make_request("a1", "A"))
        scheduler.enqueue(_make_request("b0", "B"))

        # A was first, B added later. Rotation: A, B
        assert scheduler.dequeue().client_id == "A"
        assert scheduler.dequeue().client_id == "B"
        assert scheduler.dequeue().client_id == "A"

    def test_uneven_client_queues(self):
        """Client with fewer requests exhausts first, others continue."""
        scheduler = FairScheduler(max_size=20)
        # A has 1, B has 3
        scheduler.enqueue(_make_request("a0", "A"))
        for i in range(3):
            scheduler.enqueue(_make_request(f"b{i}", "B"))

        # Round-robin: A, B, (A empty → removed), B, B
        results = [scheduler.dequeue().client_id for _ in range(4)]
        assert results == ["A", "B", "B", "B"]

    def test_interleaved_enqueue_dequeue(self):
        """Enqueue and dequeue interleaved maintains fairness."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("a0", "A"))
        scheduler.enqueue(_make_request("b0", "B"))

        assert scheduler.dequeue().client_id == "A"
        # A is now empty, removed from rotation. Add more for A.
        scheduler.enqueue(_make_request("a1", "A"))
        # Now B is front of rotation, A re-added at end
        assert scheduler.dequeue().client_id == "B"
        assert scheduler.dequeue().client_id == "A"


class TestFairSchedulerStats:
    """Tests for FairScheduler.get_stats()."""

    def test_initial_stats_all_zero(self):
        """Fresh scheduler has all-zero stats."""
        scheduler = FairScheduler(max_size=10)
        stats = scheduler.get_stats()
        assert stats.pending == 0
        assert stats.active == 0
        assert stats.completed == 0
        assert stats.rejected == 0
        assert stats.avg_wait_time == 0.0

    def test_stats_pending_reflects_queue_size(self):
        """Stats.pending matches the current queue size."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "B"))
        stats = scheduler.get_stats()
        assert stats.pending == 2

    def test_stats_after_enqueue_and_dequeue(self):
        """Stats reflect correct state after mixed operations."""
        scheduler = FairScheduler(max_size=5)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        scheduler.dequeue()
        stats = scheduler.get_stats()
        assert stats.pending == 1
        assert stats.completed == 1
        assert stats.rejected == 0

    def test_stats_active_always_zero(self):
        """Stats.active is always 0 (tracked externally by orchestrator)."""
        scheduler = FairScheduler(max_size=10)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.dequeue()
        assert scheduler.get_stats().active == 0


class TestFairSchedulerIsFull:
    """Tests for FairScheduler.is_full()."""

    def test_not_full_when_empty(self):
        """Empty scheduler is not full."""
        scheduler = FairScheduler(max_size=10)
        assert scheduler.is_full() is False

    def test_not_full_below_capacity(self):
        """Scheduler below max_size is not full."""
        scheduler = FairScheduler(max_size=5)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        assert scheduler.is_full() is False

    def test_full_at_capacity(self):
        """Scheduler at max_size is full."""
        scheduler = FairScheduler(max_size=3)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "B"))
        scheduler.enqueue(_make_request("r3", "C"))
        assert scheduler.is_full() is True

    def test_not_full_after_dequeue(self):
        """Scheduler becomes not full after dequeue."""
        scheduler = FairScheduler(max_size=2)
        scheduler.enqueue(_make_request("r1", "A"))
        scheduler.enqueue(_make_request("r2", "A"))
        assert scheduler.is_full() is True
        scheduler.dequeue()
        assert scheduler.is_full() is False


# ==============================================================================
# RequestQueueOrchestrator Tests
# ==============================================================================

from kiro.request_queue import RequestQueueOrchestrator
from kiro.exceptions import QueueTimeoutError, QueueFullError


def _make_orchestrator(
    max_concurrent: int = 2, max_size: int = 50, timeout: float = 30.0
) -> RequestQueueOrchestrator:
    """Helper to create an orchestrator with custom settings."""
    limiter = ConcurrencyLimiter(max_concurrent=max_concurrent)
    scheduler = FairScheduler(max_size=max_size)
    return RequestQueueOrchestrator(
        concurrency_limiter=limiter,
        fair_scheduler=scheduler,
        queue_timeout=timeout,
    )


class TestOrchestratorSubmitBasic:
    """Tests for RequestQueueOrchestrator.submit() basic behavior."""

    @pytest.mark.asyncio
    async def test_submit_executes_callable_and_returns_result(self):
        """Submit executes the provided callable and returns its result."""
        orchestrator = _make_orchestrator()
        await orchestrator.start()
        try:
            async def my_fn():
                return "hello_world"

            result = await orchestrator.submit(
                client_id="client1",
                model="claude-opus-4.6",
                account_id="acc1",
                execute_fn=my_fn,
            )
            assert result == "hello_world"
        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_submit_executes_async_callable_with_side_effects(self):
        """Submit correctly awaits async callables that have side effects."""
        orchestrator = _make_orchestrator()
        await orchestrator.start()
        try:
            side_effect = []

            async def my_fn():
                side_effect.append("executed")
                return 42

            result = await orchestrator.submit(
                client_id="c1", model="m", account_id="a1", execute_fn=my_fn
            )
            assert result == 42
            assert side_effect == ["executed"]
        finally:
            await orchestrator.stop()


class TestOrchestratorQueueFull:
    """Tests for QueueFullError when queue is at capacity."""

    @pytest.mark.asyncio
    async def test_submit_raises_queue_full_when_at_capacity(self):
        """Submit raises QueueFullError when the queue is full."""
        # max_size=1, max_concurrent=1 so first request takes the slot,
        # second fills the queue, third should be rejected
        orchestrator = _make_orchestrator(max_concurrent=1, max_size=1)
        await orchestrator.start()
        try:
            blocker = asyncio.Event()

            async def blocking_fn():
                await blocker.wait()
                return "done"

            # First request takes the concurrency slot
            task1 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c1", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)

            # Second request fills the queue (size=1)
            task2 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c2", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)

            # Third request should be rejected
            with pytest.raises(QueueFullError) as exc_info:
                await orchestrator.submit(
                    client_id="c3", model="m", account_id="a1", execute_fn=blocking_fn
                )
            assert exc_info.value.max_size == 1

            # Cleanup
            blocker.set()
            await task1
            await task2
        finally:
            await orchestrator.stop()


class TestOrchestratorTimeout:
    """Tests for QueueTimeoutError when request waits too long."""

    @pytest.mark.asyncio
    async def test_submit_raises_timeout_when_waiting_too_long(self):
        """Submit raises QueueTimeoutError when queue wait exceeds timeout."""
        # Very short timeout so test runs fast
        orchestrator = _make_orchestrator(max_concurrent=1, max_size=10, timeout=0.1)
        await orchestrator.start()
        try:
            blocker = asyncio.Event()

            async def blocking_fn():
                await blocker.wait()
                return "done"

            # First request takes the slot and blocks
            task1 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c1", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)

            # Second request enters queue but will timeout
            with pytest.raises(QueueTimeoutError) as exc_info:
                await orchestrator.submit(
                    client_id="c2", model="m", account_id="a1", execute_fn=blocking_fn
                )
            assert exc_info.value.wait_time >= 0.1

            # Cleanup
            blocker.set()
            await task1
        finally:
            await orchestrator.stop()


class TestOrchestratorConcurrencyLimit:
    """Tests for concurrency limiting behavior."""

    @pytest.mark.asyncio
    async def test_concurrent_submits_respect_concurrency_limit(self):
        """Only N requests execute simultaneously per account."""
        orchestrator = _make_orchestrator(max_concurrent=2, max_size=10)
        await orchestrator.start()
        try:
            max_concurrent_observed = 0
            current_concurrent = 0
            lock = asyncio.Lock()

            async def tracking_fn():
                nonlocal max_concurrent_observed, current_concurrent
                async with lock:
                    current_concurrent += 1
                    max_concurrent_observed = max(
                        max_concurrent_observed, current_concurrent
                    )
                await asyncio.sleep(0.1)
                async with lock:
                    current_concurrent -= 1
                return "ok"

            # Submit 4 requests on same account (limit=2)
            tasks = [
                asyncio.create_task(
                    orchestrator.submit(
                        client_id="c1",
                        model="m",
                        account_id="a1",
                        execute_fn=tracking_fn,
                    )
                )
                for _ in range(4)
            ]
            results = await asyncio.gather(*tasks)
            assert all(r == "ok" for r in results)
            assert max_concurrent_observed <= 2
        finally:
            await orchestrator.stop()


class TestOrchestratorFairScheduling:
    """Tests for fair scheduling across clients."""

    @pytest.mark.asyncio
    async def test_two_clients_get_interleaved_execution(self):
        """Requests from 2 clients are interleaved fairly."""
        orchestrator = _make_orchestrator(max_concurrent=1, max_size=10, timeout=5.0)
        await orchestrator.start()
        try:
            execution_order = []

            async def make_fn(label: str):
                async def fn():
                    execution_order.append(label)
                    return label
                return fn

            # Submit alternating: A1, B1, A2, B2
            tasks = []
            for i in range(1, 3):
                tasks.append(
                    asyncio.create_task(
                        orchestrator.submit(
                            client_id="clientA",
                            model="m",
                            account_id="a1",
                            execute_fn=await make_fn(f"A{i}"),
                        )
                    )
                )
                tasks.append(
                    asyncio.create_task(
                        orchestrator.submit(
                            client_id="clientB",
                            model="m",
                            account_id="a1",
                            execute_fn=await make_fn(f"B{i}"),
                        )
                    )
                )

            await asyncio.gather(*tasks)

            # Fair scheduling means clients alternate: not all A's before all B's
            # With round-robin, after first executes, we should see interleaving
            a_positions = [i for i, x in enumerate(execution_order) if x.startswith("A")]
            b_positions = [i for i, x in enumerate(execution_order) if x.startswith("B")]
            # Both clients should have executed
            assert len(a_positions) == 2
            assert len(b_positions) == 2
            # They shouldn't all be grouped (A1,A2,B1,B2) — at least one B before last A
            assert not (all(a < b for a in a_positions for b in b_positions))
        finally:
            await orchestrator.stop()


class TestOrchestratorStats:
    """Tests for QueueStats tracking."""

    @pytest.mark.asyncio
    async def test_stats_updated_on_successful_completion(self):
        """Stats reflect completed requests correctly."""
        orchestrator = _make_orchestrator()
        await orchestrator.start()
        try:
            async def simple_fn():
                return "ok"

            await orchestrator.submit(
                client_id="c1", model="m", account_id="a1", execute_fn=simple_fn
            )
            await orchestrator.submit(
                client_id="c2", model="m", account_id="a1", execute_fn=simple_fn
            )

            stats = orchestrator.get_stats()
            assert stats.completed == 2
            assert stats.pending == 0
            assert stats.active == 0
        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_stats_rejected_incremented_on_queue_full(self):
        """Stats.rejected increments when queue is full."""
        orchestrator = _make_orchestrator(max_concurrent=1, max_size=1)
        await orchestrator.start()
        try:
            blocker = asyncio.Event()

            async def blocking_fn():
                await blocker.wait()
                return "done"

            # Fill slot + queue
            task1 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c1", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)
            task2 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c2", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)

            # This should be rejected
            with pytest.raises(QueueFullError):
                await orchestrator.submit(
                    client_id="c3", model="m", account_id="a1", execute_fn=blocking_fn
                )

            stats = orchestrator.get_stats()
            assert stats.rejected == 1

            blocker.set()
            await task1
            await task2
        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_stats_pending_reflects_queued_requests(self):
        """Stats.pending shows requests waiting in queue."""
        orchestrator = _make_orchestrator(max_concurrent=1, max_size=10, timeout=5.0)
        await orchestrator.start()
        try:
            blocker = asyncio.Event()

            async def blocking_fn():
                await blocker.wait()
                return "done"

            # First takes the slot
            task1 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c1", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)

            # Second goes to queue
            task2 = asyncio.create_task(
                orchestrator.submit(
                    client_id="c2", model="m", account_id="a1", execute_fn=blocking_fn
                )
            )
            await asyncio.sleep(0.05)

            stats = orchestrator.get_stats()
            assert stats.pending >= 1
            assert stats.active == 1

            blocker.set()
            await task1
            await task2
        finally:
            await orchestrator.stop()


class TestOrchestratorGracefulShutdown:
    """Tests for graceful shutdown behavior."""

    @pytest.mark.asyncio
    async def test_stop_waits_for_active_requests(self):
        """stop() waits for active requests to finish before returning."""
        orchestrator = _make_orchestrator()
        await orchestrator.start()

        completed = []

        async def slow_fn():
            await asyncio.sleep(0.1)
            completed.append("done")
            return "ok"

        task = asyncio.create_task(
            orchestrator.submit(
                client_id="c1", model="m", account_id="a1", execute_fn=slow_fn
            )
        )
        await asyncio.sleep(0.02)  # Let it start executing

        await orchestrator.stop()
        await task
        assert completed == ["done"]

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        """Calling stop() multiple times does not raise."""
        orchestrator = _make_orchestrator()
        await orchestrator.start()
        await orchestrator.stop()
        await orchestrator.stop()  # Should not raise


class TestOrchestratorExceptionPropagation:
    """Tests for exception handling in execute_fn."""

    @pytest.mark.asyncio
    async def test_exception_in_execute_fn_propagates_to_caller(self):
        """Exceptions from execute_fn propagate to the submit() caller."""
        orchestrator = _make_orchestrator()
        await orchestrator.start()
        try:
            async def failing_fn():
                raise ValueError("something broke")

            with pytest.raises(ValueError, match="something broke"):
                await orchestrator.submit(
                    client_id="c1", model="m", account_id="a1", execute_fn=failing_fn
                )
        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_exception_in_execute_fn_releases_slot(self):
        """Concurrency slot is released even when execute_fn raises."""
        orchestrator = _make_orchestrator(max_concurrent=1)
        await orchestrator.start()
        try:
            async def failing_fn():
                raise RuntimeError("oops")

            async def success_fn():
                return "ok"

            # First call fails
            with pytest.raises(RuntimeError):
                await orchestrator.submit(
                    client_id="c1", model="m", account_id="a1", execute_fn=failing_fn
                )

            # Second call should succeed (slot was released)
            result = await orchestrator.submit(
                client_id="c1", model="m", account_id="a1", execute_fn=success_fn
            )
            assert result == "ok"
        finally:
            await orchestrator.stop()
