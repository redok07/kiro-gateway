# -*- coding: utf-8 -*-

"""
Tests for concurrency tracking methods in kiro/account_manager.py.

Tests the 4 new methods added to AccountManager:
- get_account_load: return active_requests for an account
- get_least_loaded_account: pick account with fewest active_requests
- increment_active: account.active_requests += 1
- decrement_active: account.active_requests = max(0, active_requests - 1)
"""

import asyncio
import time

import pytest
from unittest.mock import MagicMock, patch

from kiro.account_manager import Account, AccountStats, AccountManager
from kiro.queue_config import FAST_429_RECOVERY_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account(account_id: str, supported_models: list[str] | None = None) -> Account:
    """
    Create a minimal Account with a mock model_resolver.

    Args:
        account_id: Unique account identifier.
        supported_models: List of model names the account supports.
            If None, the resolver reports no models.

    Returns:
        Account instance ready for concurrency tests.
    """
    account = Account(id=account_id)
    resolver = MagicMock()
    resolver.get_available_models.return_value = supported_models or []
    account.model_resolver = resolver
    return account


def _make_manager_with_accounts(accounts: list[Account]) -> AccountManager:
    """
    Build an AccountManager whose _accounts dict is pre-populated.

    Args:
        accounts: List of Account objects to inject.

    Returns:
        AccountManager with injected accounts (no real credentials file).
    """
    manager = AccountManager.__new__(AccountManager)
    manager._accounts = {acc.id: acc for acc in accounts}
    return manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def two_accounts() -> tuple[Account, Account]:
    """Two accounts both supporting 'claude-3' model."""
    a1 = _make_account("account-1", ["claude-3"])
    a2 = _make_account("account-2", ["claude-3"])
    return a1, a2


@pytest.fixture
def manager_two(two_accounts) -> AccountManager:
    """AccountManager with two accounts supporting 'claude-3'."""
    a1, a2 = two_accounts
    return _make_manager_with_accounts([a1, a2])


# ---------------------------------------------------------------------------
# TestGetAccountLoad
# ---------------------------------------------------------------------------

class TestGetAccountLoad:
    """Tests for get_account_load method."""

    def test_returns_zero_initially(self, manager_two):
        """
        Test that a fresh account has zero active requests.

        What it does: Calls get_account_load on a new account.
        Purpose: Verify default active_requests is 0.
        """
        assert manager_two.get_account_load("account-1") == 0

    def test_returns_current_value(self, manager_two):
        """
        Test that get_account_load reflects the current active_requests value.

        What it does: Manually sets active_requests then reads via get_account_load.
        Purpose: Verify the method reads the correct field.
        """
        manager_two._accounts["account-1"].active_requests = 5
        assert manager_two.get_account_load("account-1") == 5

    def test_missing_account_returns_zero(self, manager_two):
        """
        Test graceful handling of unknown account_id.

        What it does: Calls get_account_load with a non-existent ID.
        Purpose: Verify no exception is raised; returns 0 as safe default.
        """
        assert manager_two.get_account_load("does-not-exist") == 0


# ---------------------------------------------------------------------------
# TestIncrementActive
# ---------------------------------------------------------------------------

class TestIncrementActive:
    """Tests for increment_active method."""

    def test_increments_from_zero(self, manager_two):
        """
        Test that increment_active increases active_requests from 0 to 1.

        What it does: Calls increment_active once on a fresh account.
        Purpose: Verify basic increment behaviour.
        """
        manager_two.increment_active("account-1")
        assert manager_two._accounts["account-1"].active_requests == 1

    def test_increments_multiple_times(self, manager_two):
        """
        Test that repeated calls accumulate correctly.

        What it does: Calls increment_active three times.
        Purpose: Verify counter is additive.
        """
        for _ in range(3):
            manager_two.increment_active("account-1")
        assert manager_two._accounts["account-1"].active_requests == 3

    def test_missing_account_does_not_raise(self, manager_two):
        """
        Test that increment_active is a no-op for unknown account_id.

        What it does: Calls increment_active with a non-existent ID.
        Purpose: Verify graceful handling without exceptions.
        """
        manager_two.increment_active("ghost-account")  # must not raise

    def test_increments_only_target_account(self, manager_two):
        """
        Test that incrementing one account does not affect another.

        What it does: Increments account-1 and checks account-2 is unchanged.
        Purpose: Verify per-account isolation.
        """
        manager_two.increment_active("account-1")
        assert manager_two._accounts["account-2"].active_requests == 0


# ---------------------------------------------------------------------------
# TestDecrementActive
# ---------------------------------------------------------------------------

class TestDecrementActive:
    """Tests for decrement_active method."""

    def test_decrements_from_positive(self, manager_two):
        """
        Test that decrement_active reduces active_requests by 1.

        What it does: Sets active_requests to 3, then decrements once.
        Purpose: Verify basic decrement behaviour.
        """
        manager_two._accounts["account-1"].active_requests = 3
        manager_two.decrement_active("account-1")
        assert manager_two._accounts["account-1"].active_requests == 2

    def test_never_goes_below_zero(self, manager_two):
        """
        Test that decrement_active floors at 0 and never goes negative.

        What it does: Calls decrement_active on an account with 0 active_requests.
        Purpose: Verify the max(0, ...) guard is in place.
        """
        assert manager_two._accounts["account-1"].active_requests == 0
        manager_two.decrement_active("account-1")
        assert manager_two._accounts["account-1"].active_requests == 0

    def test_missing_account_does_not_raise(self, manager_two):
        """
        Test that decrement_active is a no-op for unknown account_id.

        What it does: Calls decrement_active with a non-existent ID.
        Purpose: Verify graceful handling without exceptions.
        """
        manager_two.decrement_active("ghost-account")  # must not raise

    def test_increment_then_decrement_returns_to_zero(self, manager_two):
        """
        Test that increment followed by decrement returns to 0.

        What it does: Increments then decrements once.
        Purpose: Verify round-trip correctness.
        """
        manager_two.increment_active("account-1")
        manager_two.decrement_active("account-1")
        assert manager_two._accounts["account-1"].active_requests == 0


# ---------------------------------------------------------------------------
# TestGetLeastLoadedAccount
# ---------------------------------------------------------------------------

class TestGetLeastLoadedAccount:
    """Tests for get_least_loaded_account method."""

    def test_returns_account_with_fewer_requests(self, manager_two):
        """
        Test that the account with fewer active_requests is returned.

        What it does: Sets account-1 to 5 requests, account-2 to 1, asks for least loaded.
        Purpose: Verify selection is based on active_requests ascending.
        """
        manager_two._accounts["account-1"].active_requests = 5
        manager_two._accounts["account-2"].active_requests = 1
        result = manager_two.get_least_loaded_account("claude-3")
        assert result is not None
        assert result.id == "account-2"

    def test_returns_any_when_equal_load(self, manager_two):
        """
        Test that a valid account is returned when both have equal load.

        What it does: Both accounts at 0 requests; expects a non-None result.
        Purpose: Verify tie-breaking returns something, not None.
        """
        result = manager_two.get_least_loaded_account("claude-3")
        assert result is not None
        assert result.id in ("account-1", "account-2")

    def test_exclude_set_filters_accounts(self, manager_two):
        """
        Test that excluded accounts are not returned.

        What it does: Excludes account-1 (which has fewer requests) and expects account-2.
        Purpose: Verify exclude parameter is respected.
        """
        manager_two._accounts["account-1"].active_requests = 0
        manager_two._accounts["account-2"].active_requests = 10
        result = manager_two.get_least_loaded_account("claude-3", exclude={"account-1"})
        assert result is not None
        assert result.id == "account-2"

    def test_all_excluded_returns_none(self, manager_two):
        """
        Test that None is returned when all accounts are excluded.

        What it does: Excludes both accounts.
        Purpose: Verify None is returned gracefully when no candidates remain.
        """
        result = manager_two.get_least_loaded_account(
            "claude-3", exclude={"account-1", "account-2"}
        )
        assert result is None

    def test_unsupported_model_returns_none(self):
        """
        Test that None is returned when no account supports the requested model.

        What it does: Creates accounts that only support 'claude-3', asks for 'gpt-4'.
        Purpose: Verify model filtering works correctly.
        """
        a1 = _make_account("account-1", ["claude-3"])
        a2 = _make_account("account-2", ["claude-3"])
        manager = _make_manager_with_accounts([a1, a2])
        result = manager.get_least_loaded_account("gpt-4")
        assert result is None

    def test_only_supporting_account_returned(self):
        """
        Test that only the account supporting the model is returned.

        What it does: account-1 supports 'claude-3', account-2 does not.
        Purpose: Verify model filtering selects the correct account.
        """
        a1 = _make_account("account-1", ["claude-3"])
        a2 = _make_account("account-2", ["gpt-4"])
        manager = _make_manager_with_accounts([a1, a2])
        result = manager.get_least_loaded_account("claude-3")
        assert result is not None
        assert result.id == "account-1"

    def test_no_model_resolver_account_skipped(self):
        """
        Test that accounts without a model_resolver are skipped.

        What it does: Creates one account with resolver and one without.
        Purpose: Verify accounts with no resolver are not selected.
        """
        a1 = _make_account("account-1", ["claude-3"])
        a2 = Account(id="account-2")  # no model_resolver
        manager = _make_manager_with_accounts([a1, a2])
        result = manager.get_least_loaded_account("claude-3")
        assert result is not None
        assert result.id == "account-1"

    def test_empty_accounts_returns_none(self):
        """
        Test that None is returned when there are no accounts at all.

        What it does: Creates manager with empty accounts dict.
        Purpose: Verify edge case of empty account pool.
        """
        manager = _make_manager_with_accounts([])
        result = manager.get_least_loaded_account("claude-3")
        assert result is None


# ---------------------------------------------------------------------------
# Helpers for rate limit tests
# ---------------------------------------------------------------------------

def _make_manager_with_lock(accounts: list[Account]) -> AccountManager:
    """
    Build an AccountManager with _accounts and _lock for async methods.

    Args:
        accounts: List of Account objects to inject.

    Returns:
        AccountManager with injected accounts and asyncio.Lock.
    """
    manager = AccountManager.__new__(AccountManager)
    manager._accounts = {acc.id: acc for acc in accounts}
    manager._lock = asyncio.Lock()
    manager._current_account_index = 0
    manager._dirty = False
    manager._account_locks = {}
    return manager


# ---------------------------------------------------------------------------
# TestReportRateLimit
# ---------------------------------------------------------------------------

class TestReportRateLimit:
    """Tests for report_rate_limit method (429 handling separate from circuit breaker)."""

    @pytest.mark.asyncio
    async def test_sets_last_rate_limit_time(self):
        """
        Test that report_rate_limit sets last_rate_limit_time on the account.

        What it does: Calls report_rate_limit and checks the timestamp is set.
        Purpose: Verify the rate limit timestamp is recorded.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        before = time.time()
        await manager.report_rate_limit("account-1")
        after = time.time()

        assert manager._accounts["account-1"].last_rate_limit_time >= before
        assert manager._accounts["account-1"].last_rate_limit_time <= after

    @pytest.mark.asyncio
    async def test_does_not_increment_failures(self):
        """
        Test that report_rate_limit does NOT increment the failures count.

        What it does: Calls report_rate_limit and checks failures stays at 0.
        Purpose: Verify 429 is separate from circuit breaker penalty.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        await manager.report_rate_limit("account-1")

        assert manager._accounts["account-1"].failures == 0

    @pytest.mark.asyncio
    async def test_unknown_account_is_noop(self):
        """
        Test that report_rate_limit with unknown account_id does not crash.

        What it does: Calls report_rate_limit with a non-existent ID.
        Purpose: Verify graceful handling of unknown accounts.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        # Should not raise
        await manager.report_rate_limit("ghost-account")

    @pytest.mark.asyncio
    async def test_retry_after_larger_than_default(self):
        """
        Test that retry_after > FAST_429_RECOVERY_SECONDS is respected.

        What it does: Calls report_rate_limit with retry_after=10.0.
        Purpose: Verify the method accepts retry_after without error.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        # Should not raise; just sets the timestamp
        await manager.report_rate_limit("account-1", retry_after=10.0)
        assert manager._accounts["account-1"].last_rate_limit_time > 0


# ---------------------------------------------------------------------------
# TestIsRateLimited
# ---------------------------------------------------------------------------

class TestIsRateLimited:
    """Tests for is_rate_limited method."""

    @pytest.mark.asyncio
    async def test_returns_true_within_cooldown(self):
        """
        Test that is_rate_limited returns True within the cooldown window.

        What it does: Sets last_rate_limit_time to now, checks immediately.
        Purpose: Verify account is considered rate-limited during cooldown.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        manager._accounts["account-1"].last_rate_limit_time = time.time()

        result = await manager.is_rate_limited("account-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_after_cooldown_expires(self):
        """
        Test that is_rate_limited returns False after cooldown expires.

        What it does: Sets last_rate_limit_time to past (beyond FAST_429_RECOVERY_SECONDS).
        Purpose: Verify account recovers after cooldown.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        # Set rate limit time to well in the past
        manager._accounts["account-1"].last_rate_limit_time = (
            time.time() - FAST_429_RECOVERY_SECONDS - 1.0
        )

        result = await manager.is_rate_limited("account-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_never_rate_limited(self):
        """
        Test that is_rate_limited returns False for account never rate-limited.

        What it does: Checks a fresh account with last_rate_limit_time=0.0.
        Purpose: Verify default state is not rate-limited.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        result = await manager.is_rate_limited("account-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_account(self):
        """
        Test that is_rate_limited returns False for unknown account_id.

        What it does: Calls is_rate_limited with a non-existent ID.
        Purpose: Verify graceful handling returns False (not an error).
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        result = await manager.is_rate_limited("ghost-account")
        assert result is False

    @pytest.mark.asyncio
    async def test_resets_timestamp_after_recovery(self):
        """
        Test that is_rate_limited resets last_rate_limit_time after recovery.

        What it does: Sets expired rate limit, calls is_rate_limited, checks reset.
        Purpose: Verify cleanup of stale rate limit state.
        """
        a1 = _make_account("account-1", ["claude-3"])
        manager = _make_manager_with_lock([a1])

        manager._accounts["account-1"].last_rate_limit_time = (
            time.time() - FAST_429_RECOVERY_SECONDS - 1.0
        )

        await manager.is_rate_limited("account-1")
        assert manager._accounts["account-1"].last_rate_limit_time == 0.0


# ---------------------------------------------------------------------------
# TestGetNextAccountRateLimitPreference
# ---------------------------------------------------------------------------

class TestGetNextAccountRateLimitPreference:
    """Tests for get_next_account preferring non-rate-limited accounts."""

    @pytest.mark.asyncio
    async def test_prefers_non_rate_limited_account(self):
        """
        Test that get_next_account prefers non-rate-limited accounts.

        What it does: Rate-limits account-1, expects account-2 to be selected.
        Purpose: Verify rate-limited accounts are deprioritized.
        """
        a1 = _make_account("account-1", ["claude-3"])
        a1.auth_manager = MagicMock()
        a2 = _make_account("account-2", ["claude-3"])
        a2.auth_manager = MagicMock()
        manager = _make_manager_with_lock([a1, a2])

        # Rate-limit account-1
        manager._accounts["account-1"].last_rate_limit_time = time.time()

        result = await manager.get_next_account("claude-3")
        assert result is not None
        assert result.id == "account-2"

    @pytest.mark.asyncio
    async def test_returns_rate_limited_as_last_resort(self):
        """
        Test that get_next_account returns rate-limited account when all are rate-limited.

        What it does: Rate-limits both accounts, expects one to still be returned.
        Purpose: Verify rate-limited accounts are used as last resort (not excluded).
        """
        a1 = _make_account("account-1", ["claude-3"])
        a1.auth_manager = MagicMock()
        a2 = _make_account("account-2", ["claude-3"])
        a2.auth_manager = MagicMock()
        manager = _make_manager_with_lock([a1, a2])

        # Rate-limit both accounts
        manager._accounts["account-1"].last_rate_limit_time = time.time()
        manager._accounts["account-2"].last_rate_limit_time = time.time()

        result = await manager.get_next_account("claude-3")
        assert result is not None
        assert result.id in ("account-1", "account-2")
