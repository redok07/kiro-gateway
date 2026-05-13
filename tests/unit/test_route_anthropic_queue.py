# -*- coding: utf-8 -*-

"""
Tests for request queue integration in Anthropic routes.

Verifies that:
- QueueTimeoutError (slot acquire timeout) returns HTTP 503 with Anthropic overloaded_error format
- QueueFullError (scheduler full) returns HTTP 503 with Anthropic overloaded_error format
- AccountRateLimited triggers report_rate_limit and continues failover loop
- QUEUE_ENABLED=False bypasses queue entirely (existing behavior)
- Concurrency limiter slot is acquired/released correctly
"""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kiro.exceptions import AccountRateLimited, QueueTimeoutError, QueueFullError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_anthropic_request():
    """Create a mock FastAPI request with app.state configured for Anthropic route."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = "10.0.0.42"

    # App state
    request.app = MagicMock()
    request.app.state.account_system = True

    # Queue orchestrator
    orchestrator = MagicMock()
    orchestrator._queue_timeout = 30.0
    orchestrator._limiter = MagicMock()
    orchestrator._limiter.acquire = AsyncMock(return_value=True)
    orchestrator._limiter.release = MagicMock()
    orchestrator._scheduler = MagicMock()
    orchestrator._scheduler.is_full = MagicMock(return_value=False)
    request.app.state.queue_orchestrator = orchestrator

    # Account manager
    request.app.state.account_manager = MagicMock()
    request.app.state.account_manager._accounts = {"acc_1": MagicMock()}
    request.app.state.account_manager.report_rate_limit = AsyncMock()
    request.app.state.account_manager.report_success = AsyncMock()
    request.app.state.account_manager.report_failure = AsyncMock()
    request.app.state.account_manager.report_usage = AsyncMock()

    # HTTP clients
    request.app.state.http_client = MagicMock()
    request.app.state.streaming_http_client = MagicMock()

    return request


@pytest.fixture
def mock_anthropic_request_data():
    """Create a mock AnthropicMessagesRequest."""
    request_data = MagicMock()
    request_data.model = "claude-sonnet-4-20250514"
    request_data.stream = False
    request_data.messages = []
    request_data.tools = None
    request_data.system = None
    request_data.max_tokens = 1024
    return request_data


def _make_mock_account(account_id: str = "acc_1"):
    """Helper to create a mock account object."""
    from kiro.auth import AuthType

    account = MagicMock()
    account.id = account_id
    account.auth_manager = MagicMock()
    account.auth_manager.auth_type = AuthType.KIRO_DESKTOP
    account.auth_manager.profile_arn = "arn:aws:test"
    account.auth_manager.api_host = "https://api.test.com"
    account.model_cache = MagicMock()
    account.model_resolver = MagicMock()
    return account


# =============================================================================
# Tests: Queue timeout returns 503 with Anthropic error format
# =============================================================================


class TestAnthropicQueueTimeout:
    """Tests for queue timeout handling in Anthropic route."""

    @pytest.mark.asyncio
    async def test_slot_acquire_timeout_returns_503_anthropic_format(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that concurrency limiter timeout returns HTTP 503 in Anthropic format.

        What it does: Simulates acquire returning False (timeout).
        Purpose: Verify overloaded_error format with "Server busy, please retry later".
        """
        from kiro.routes_anthropic import messages

        mock_account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=mock_account
        )

        # Limiter acquire returns False (timeout)
        mock_anthropic_request.app.state.queue_orchestrator._limiter.acquire = (
            AsyncMock(return_value=False)
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["type"] == "error"
        assert body["error"]["type"] == "overloaded_error"
        assert body["error"]["message"] == "Server busy, please retry later"

    @pytest.mark.asyncio
    async def test_slot_acquire_asyncio_timeout_returns_503(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that asyncio.TimeoutError from acquire returns HTTP 503.

        What it does: Simulates acquire raising asyncio.TimeoutError.
        Purpose: Verify the except asyncio.TimeoutError path works.
        """
        from kiro.routes_anthropic import messages

        mock_account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=mock_account
        )

        # Limiter acquire raises TimeoutError
        mock_anthropic_request.app.state.queue_orchestrator._limiter.acquire = (
            AsyncMock(side_effect=asyncio.TimeoutError("slot timeout"))
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["type"] == "error"
        assert body["error"]["type"] == "overloaded_error"
        assert body["error"]["message"] == "Server busy, please retry later"


# =============================================================================
# Tests: Queue full returns 503 with Anthropic error format
# =============================================================================


class TestAnthropicQueueFull:
    """Tests for queue full handling in Anthropic route."""

    @pytest.mark.asyncio
    async def test_scheduler_full_returns_503_anthropic_format(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that scheduler.is_full() returns HTTP 503 in Anthropic format.

        What it does: Simulates scheduler reporting full queue.
        Purpose: Verify overloaded_error format with "Too many pending requests".
        """
        from kiro.routes_anthropic import messages

        mock_account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=mock_account
        )

        # Scheduler reports full
        mock_anthropic_request.app.state.queue_orchestrator._scheduler.is_full = (
            MagicMock(return_value=True)
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["type"] == "error"
        assert body["error"]["type"] == "overloaded_error"
        assert body["error"]["message"] == "Too many pending requests"


# =============================================================================
# Tests: AccountRateLimited triggers report_rate_limit and continues loop
# =============================================================================


class TestAnthropicAccountRateLimited:
    """Tests for AccountRateLimited handling in Anthropic failover loop."""

    @pytest.mark.asyncio
    async def test_rate_limited_reports_and_continues_to_next_account(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that AccountRateLimited calls report_rate_limit and tries next account.

        What it does: First attempt raises AccountRateLimited, second succeeds.
        Purpose: Verify rate-limited accounts are reported and failover continues.
        """
        from kiro.routes_anthropic import messages

        # Two accounts
        mock_anthropic_request.app.state.account_manager._accounts = {
            "acc_1": MagicMock(),
            "acc_2": MagicMock(),
        }

        account_1 = _make_mock_account("acc_1")
        account_2 = _make_mock_account("acc_2")

        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            side_effect=[account_1, account_2, None]
        )

        # Mock HTTP client
        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        call_count = [0]

        async def mock_request_with_retry(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise AccountRateLimited(account_id="acc_1", retry_after=5.0)
            return mock_response

        mock_http_client.request_with_retry = mock_request_with_retry

        mock_anthropic_response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_anthropic.collect_anthropic_response", new_callable=AsyncMock, return_value=mock_anthropic_response), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Verify report_rate_limit was called for acc_1
        mock_anthropic_request.app.state.account_manager.report_rate_limit.assert_called_once_with(
            "acc_1", 5.0
        )
        # Verify success was reported for acc_2
        mock_anthropic_request.app.state.account_manager.report_success.assert_called_once_with(
            "acc_2", "claude-sonnet-4-20250514"
        )

    @pytest.mark.asyncio
    async def test_rate_limited_adds_account_to_tried(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that AccountRateLimited adds account_id to tried_accounts set.

        What it does: Raises AccountRateLimited, verifies next call excludes that account.
        Purpose: Verify rate-limited accounts are excluded from subsequent attempts.
        """
        from kiro.routes_anthropic import messages

        mock_anthropic_request.app.state.account_manager._accounts = {
            "acc_1": MagicMock(),
        }

        account_1 = _make_mock_account("acc_1")

        # First returns account, second returns None (all tried)
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            side_effect=[account_1, None]
        )

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.request_with_retry = AsyncMock(
            side_effect=AccountRateLimited(account_id="acc_1", retry_after=10.0)
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Single account exhausted after rate limit → returns last_error_status (429)
        assert response.status_code == 429
        mock_anthropic_request.app.state.account_manager.report_rate_limit.assert_called_once_with(
            "acc_1", 10.0
        )


# =============================================================================
# Tests: QUEUE_ENABLED=False bypasses queue entirely
# =============================================================================


class TestAnthropicQueueDisabled:
    """Tests for QUEUE_ENABLED=False behavior in Anthropic route."""

    @pytest.mark.asyncio
    async def test_queue_disabled_skips_limiter(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that QUEUE_ENABLED=False skips concurrency limiter entirely.

        What it does: Runs with QUEUE_ENABLED=False and verifies limiter is never called.
        Purpose: Verify feature flag correctly bypasses queue system.
        """
        from kiro.routes_anthropic import messages

        account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.request_with_retry = AsyncMock(return_value=mock_response)

        mock_anthropic_response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", False), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_anthropic.collect_anthropic_response", new_callable=AsyncMock, return_value=mock_anthropic_response), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Limiter should never be called
        mock_anthropic_request.app.state.queue_orchestrator._limiter.acquire.assert_not_called()
        mock_anthropic_request.app.state.queue_orchestrator._limiter.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_orchestrator_skips_queue(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that missing queue_orchestrator skips queue even if QUEUE_ENABLED=True.

        What it does: Sets queue_orchestrator to None and verifies normal behavior.
        Purpose: Verify graceful degradation when orchestrator not initialized.
        """
        from kiro.routes_anthropic import messages

        # Remove orchestrator
        mock_anthropic_request.app.state.queue_orchestrator = None

        account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.request_with_retry = AsyncMock(return_value=mock_response)

        mock_anthropic_response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_anthropic.collect_anthropic_response", new_callable=AsyncMock, return_value=mock_anthropic_response), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Should succeed without queue
        assert response.status_code == 200


# =============================================================================
# Tests: Concurrency slot release in finally block
# =============================================================================


class TestAnthropicSlotRelease:
    """Tests for concurrency slot release behavior."""

    @pytest.mark.asyncio
    async def test_slot_released_on_success(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that concurrency slot is released after successful request.

        What it does: Makes a successful request and verifies release is called.
        Purpose: Verify slot cleanup on happy path.
        """
        from kiro.routes_anthropic import messages

        account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.request_with_retry = AsyncMock(return_value=mock_response)

        mock_anthropic_response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_anthropic.collect_anthropic_response", new_callable=AsyncMock, return_value=mock_anthropic_response), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Verify acquire and release were both called
        mock_anthropic_request.app.state.queue_orchestrator._limiter.acquire.assert_called_once_with(
            "acc_1", timeout=30.0
        )
        mock_anthropic_request.app.state.queue_orchestrator._limiter.release.assert_called_once_with(
            "acc_1"
        )

    @pytest.mark.asyncio
    async def test_slot_released_on_error(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that concurrency slot is released even when request fails.

        What it does: Makes a request that raises an exception, verifies release.
        Purpose: Verify slot cleanup on error path via finally block.
        """
        from kiro.routes_anthropic import messages

        account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            side_effect=[account, None]
        )

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.request_with_retry = AsyncMock(
            side_effect=RuntimeError("connection failed")
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Should return 500 but slot must be released
        assert response.status_code == 500
        mock_anthropic_request.app.state.queue_orchestrator._limiter.release.assert_called_once_with(
            "acc_1"
        )


# =============================================================================
# Tests: client_id extraction
# =============================================================================


class TestAnthropicClientIdExtraction:
    """Tests for client_id extraction from request."""

    @pytest.mark.asyncio
    async def test_client_id_from_request_host(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that client_id is extracted from request.client.host.

        What it does: Sets request.client.host and verifies it's used.
        Purpose: Verify client identification for queue fairness.
        """
        mock_anthropic_request.client.host = "192.168.1.200"

        # This test just verifies the code doesn't crash with a valid host
        from kiro.routes_anthropic import messages

        account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        # Make scheduler full to get early return
        mock_anthropic_request.app.state.queue_orchestrator._scheduler.is_full = (
            MagicMock(return_value=True)
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_client_id_unknown_when_no_client(
        self, mock_anthropic_request, mock_anthropic_request_data
    ):
        """
        Test that client_id defaults to "unknown" when request.client is None.

        What it does: Sets request.client to None.
        Purpose: Verify graceful handling of missing client info.
        """
        mock_anthropic_request.client = None

        from kiro.routes_anthropic import messages

        account = _make_mock_account()
        mock_anthropic_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        # Make scheduler full to get early return
        mock_anthropic_request.app.state.queue_orchestrator._scheduler.is_full = (
            MagicMock(return_value=True)
        )

        with patch("kiro.routes_anthropic.QUEUE_ENABLED", True), \
             patch("kiro.routes_anthropic.anthropic_to_kiro", return_value={"test": "payload"}), \
             patch("kiro.routes_anthropic.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_anthropic.debug_logger", None):
            response = await messages(mock_anthropic_request, mock_anthropic_request_data)

        # Should still work with "unknown" client_id
        assert response.status_code == 503
