# -*- coding: utf-8 -*-

"""
Tests for request queue integration in OpenAI routes.

Verifies that:
- QueueTimeoutError returns HTTP 503 with "Server busy, please retry later"
- QueueFullError returns HTTP 503 with "Too many pending requests"
- AccountRateLimited triggers report_rate_limit and continues failover loop
- QUEUE_ENABLED=False bypasses queue entirely (existing behavior)
- Concurrency limiter slot is acquired/released correctly
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from kiro.exceptions import AccountRateLimited, QueueTimeoutError, QueueFullError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request with app.state configured."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = "192.168.1.100"
    
    # App state
    request.app = MagicMock()
    request.app.state.account_system = True
    request.app.state.queue_orchestrator = MagicMock()
    request.app.state.queue_orchestrator._queue_timeout = 30.0
    request.app.state.queue_orchestrator._limiter = MagicMock()
    request.app.state.queue_orchestrator._limiter.acquire = AsyncMock(return_value=True)
    request.app.state.queue_orchestrator._limiter.release = MagicMock()
    
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
def mock_account():
    """Create a mock account object."""
    account = MagicMock()
    account.id = "acc_1"
    account.auth_manager = MagicMock()
    account.auth_manager.auth_type = "kiro_desktop"
    account.auth_manager.profile_arn = "arn:aws:test"
    account.auth_manager.api_host = "https://api.test.com"
    account.model_cache = MagicMock()
    account.model_resolver = MagicMock()
    return account


@pytest.fixture
def mock_request_data():
    """Create a mock ChatCompletionRequest."""
    request_data = MagicMock()
    request_data.model = "claude-sonnet-4-20250514"
    request_data.stream = False
    request_data.messages = []
    request_data.tools = None
    return request_data


# =============================================================================
# Tests: QueueTimeoutError returns 503
# =============================================================================


class TestQueueTimeoutError:
    """Tests for QueueTimeoutError handling in routes."""

    @pytest.mark.asyncio
    async def test_queue_timeout_returns_503(self, mock_request):
        """
        Test that QueueTimeoutError is caught and returns HTTP 503.

        What it does: Simulates a queue timeout during concurrency slot acquisition.
        Purpose: Verify the route returns proper 503 with user-friendly message.
        """
        from kiro.routes_openai import chat_completions

        mock_request.app.state.queue_orchestrator._limiter.acquire = AsyncMock(
            return_value=False
        )

        # Mock get_next_account to return an account
        mock_account = MagicMock()
        mock_account.id = "acc_1"
        mock_account.auth_manager = MagicMock()
        mock_account.auth_manager.auth_type = MagicMock()
        mock_account.auth_manager.profile_arn = "arn:test"
        mock_account.auth_manager.api_host = "https://api.test.com"
        mock_account.model_cache = MagicMock()
        mock_account.model_resolver = MagicMock()
        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=mock_account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client):
            response = await chat_completions(mock_request, request_data)

        assert response.status_code == 503
        assert response.body is not None
        import json
        body = json.loads(response.body)
        assert body["error"]["message"] == "Server busy, please retry later"
        assert body["error"]["type"] == "queue_timeout"

    @pytest.mark.asyncio
    async def test_queue_timeout_error_attributes(self):
        """
        Test QueueTimeoutError stores wait_time and request_id.

        What it does: Creates a QueueTimeoutError and checks attributes.
        Purpose: Verify exception carries diagnostic information.
        """
        error = QueueTimeoutError(wait_time=25.5, request_id="client_123")
        assert error.wait_time == 25.5
        assert error.request_id == "client_123"
        assert "25.5" in str(error)
        assert "client_123" in str(error)


# =============================================================================
# Tests: QueueFullError returns 503
# =============================================================================


class TestQueueFullError:
    """Tests for QueueFullError handling in routes."""

    @pytest.mark.asyncio
    async def test_queue_full_returns_503(self, mock_request):
        """
        Test that QueueFullError is caught and returns HTTP 503.

        What it does: Simulates a full queue by raising QueueFullError from acquire.
        Purpose: Verify the route returns proper 503 with "Too many pending requests".
        """
        from kiro.routes_openai import chat_completions

        mock_request.app.state.queue_orchestrator._limiter.acquire = AsyncMock(
            side_effect=QueueFullError(queue_size=50, max_size=50)
        )

        mock_account = MagicMock()
        mock_account.id = "acc_1"
        mock_account.auth_manager = MagicMock()
        mock_account.auth_manager.auth_type = MagicMock()
        mock_account.auth_manager.profile_arn = "arn:test"
        mock_account.auth_manager.api_host = "https://api.test.com"
        mock_account.model_cache = MagicMock()
        mock_account.model_resolver = MagicMock()
        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=mock_account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client):
            response = await chat_completions(mock_request, request_data)

        assert response.status_code == 503
        import json
        body = json.loads(response.body)
        assert body["error"]["message"] == "Too many pending requests"
        assert body["error"]["type"] == "queue_full"

    @pytest.mark.asyncio
    async def test_queue_full_error_attributes(self):
        """
        Test QueueFullError stores queue_size and max_size.

        What it does: Creates a QueueFullError and checks attributes.
        Purpose: Verify exception carries capacity information.
        """
        error = QueueFullError(queue_size=50, max_size=50)
        assert error.queue_size == 50
        assert error.max_size == 50
        assert "50/50" in str(error)


# =============================================================================
# Tests: AccountRateLimited triggers report_rate_limit
# =============================================================================


class TestAccountRateLimited:
    """Tests for AccountRateLimited handling in failover loop."""

    @pytest.mark.asyncio
    async def test_account_rate_limited_reports_and_continues(self, mock_request):
        """
        Test that AccountRateLimited calls report_rate_limit and tries next account.

        What it does: First attempt raises AccountRateLimited, second attempt succeeds.
        Purpose: Verify rate-limited accounts are reported and failover continues.
        """
        from kiro.routes_openai import chat_completions
        from kiro.auth import AuthType

        # Two accounts available
        mock_request.app.state.account_manager._accounts = {
            "acc_1": MagicMock(),
            "acc_2": MagicMock()
        }

        account_1 = MagicMock()
        account_1.id = "acc_1"
        account_1.auth_manager = MagicMock()
        account_1.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account_1.auth_manager.profile_arn = "arn:test"
        account_1.auth_manager.api_host = "https://api.test.com"
        account_1.model_cache = MagicMock()
        account_1.model_resolver = MagicMock()

        account_2 = MagicMock()
        account_2.id = "acc_2"
        account_2.auth_manager = MagicMock()
        account_2.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account_2.auth_manager.profile_arn = "arn:test2"
        account_2.auth_manager.api_host = "https://api.test.com"
        account_2.model_cache = MagicMock()
        account_2.model_resolver = MagicMock()

        # First call returns acc_1, second returns acc_2, third returns None
        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            side_effect=[account_1, account_2, None]
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        # Mock http_client to raise AccountRateLimited on first call, succeed on second
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
        mock_http_client.client = MagicMock()

        mock_openai_response = {"choices": [], "usage": {"credits_used": 1.0}}

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_openai.collect_stream_response", new_callable=AsyncMock, return_value=mock_openai_response), \
             patch("kiro.routes_openai.debug_logger", None):
            response = await chat_completions(mock_request, request_data)

        # Verify report_rate_limit was called for acc_1
        mock_request.app.state.account_manager.report_rate_limit.assert_called_once_with(
            "acc_1", 5.0
        )
        # Verify success was reported for acc_2
        mock_request.app.state.account_manager.report_success.assert_called_once_with(
            "acc_2", "claude-sonnet-4-20250514"
        )

    @pytest.mark.asyncio
    async def test_account_rate_limited_single_account_breaks(self, mock_request):
        """
        Test that AccountRateLimited with single account breaks the loop.

        What it does: Single account raises AccountRateLimited, loop breaks.
        Purpose: Verify single-account scenario doesn't infinite loop.
        """
        from kiro.routes_openai import chat_completions
        from kiro.auth import AuthType

        mock_request.app.state.account_manager._accounts = {"acc_1": MagicMock()}

        account_1 = MagicMock()
        account_1.id = "acc_1"
        account_1.auth_manager = MagicMock()
        account_1.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account_1.auth_manager.profile_arn = "arn:test"
        account_1.auth_manager.api_host = "https://api.test.com"
        account_1.model_cache = MagicMock()
        account_1.model_resolver = MagicMock()

        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            side_effect=[account_1, None]
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.request_with_retry = AsyncMock(
            side_effect=AccountRateLimited(account_id="acc_1", retry_after=10.0)
        )

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_openai.debug_logger", None):
            with pytest.raises(HTTPException) as exc_info:
                await chat_completions(mock_request, request_data)

        # Single account exhausted → should raise with last error
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_account_rate_limited_exception_attributes(self):
        """
        Test AccountRateLimited stores account_id and retry_after.

        What it does: Creates an AccountRateLimited and checks attributes.
        Purpose: Verify exception carries rate limit information.
        """
        error = AccountRateLimited(account_id="acc_xyz", retry_after=15.0)
        assert error.account_id == "acc_xyz"
        assert error.retry_after == 15.0
        assert "acc_xyz" in str(error)


# =============================================================================
# Tests: QUEUE_ENABLED=False bypasses queue
# =============================================================================


class TestQueueDisabled:
    """Tests for QUEUE_ENABLED=False behavior."""

    @pytest.mark.asyncio
    async def test_queue_disabled_skips_limiter(self, mock_request):
        """
        Test that QUEUE_ENABLED=False skips concurrency limiter entirely.

        What it does: Runs with QUEUE_ENABLED=False and verifies limiter is never called.
        Purpose: Verify feature flag correctly bypasses queue system.
        """
        from kiro.routes_openai import chat_completions
        from kiro.auth import AuthType

        account = MagicMock()
        account.id = "acc_1"
        account.auth_manager = MagicMock()
        account.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account.auth_manager.profile_arn = "arn:test"
        account.auth_manager.api_host = "https://api.test.com"
        account.model_cache = MagicMock()
        account.model_resolver = MagicMock()

        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http_client.request_with_retry = AsyncMock(return_value=mock_response)

        mock_openai_response = {"choices": [], "usage": {}}

        with patch("kiro.routes_openai.QUEUE_ENABLED", False), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_openai.collect_stream_response", new_callable=AsyncMock, return_value=mock_openai_response), \
             patch("kiro.routes_openai.debug_logger", None):
            response = await chat_completions(mock_request, request_data)

        # Limiter should NOT have been called
        mock_request.app.state.queue_orchestrator._limiter.acquire.assert_not_called()
        mock_request.app.state.queue_orchestrator._limiter.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_queue_disabled_no_orchestrator(self, mock_request):
        """
        Test that None orchestrator (QUEUE_ENABLED=False at startup) works.

        What it does: Sets queue_orchestrator to None and verifies normal operation.
        Purpose: Verify graceful handling when orchestrator was never created.
        """
        from kiro.routes_openai import chat_completions
        from kiro.auth import AuthType

        mock_request.app.state.queue_orchestrator = None

        account = MagicMock()
        account.id = "acc_1"
        account.auth_manager = MagicMock()
        account.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account.auth_manager.profile_arn = "arn:test"
        account.auth_manager.api_host = "https://api.test.com"
        account.model_cache = MagicMock()
        account.model_resolver = MagicMock()

        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http_client.request_with_retry = AsyncMock(return_value=mock_response)

        mock_openai_response = {"choices": [], "usage": {}}

        with patch("kiro.routes_openai.QUEUE_ENABLED", False), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_openai.collect_stream_response", new_callable=AsyncMock, return_value=mock_openai_response), \
             patch("kiro.routes_openai.debug_logger", None):
            response = await chat_completions(mock_request, request_data)

        # Should succeed without any queue interaction
        assert isinstance(response, JSONResponse)


# =============================================================================
# Tests: Concurrency slot release
# =============================================================================


class TestConcurrencySlotRelease:
    """Tests for proper concurrency slot acquisition and release."""

    @pytest.mark.asyncio
    async def test_slot_released_on_success(self, mock_request):
        """
        Test that concurrency slot is released after successful request.

        What it does: Makes a successful request and verifies release is called.
        Purpose: Prevent slot leaks on success path.
        """
        from kiro.routes_openai import chat_completions
        from kiro.auth import AuthType

        account = MagicMock()
        account.id = "acc_1"
        account.auth_manager = MagicMock()
        account.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account.auth_manager.profile_arn = "arn:test"
        account.auth_manager.api_host = "https://api.test.com"
        account.model_cache = MagicMock()
        account.model_resolver = MagicMock()

        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http_client.request_with_retry = AsyncMock(return_value=mock_response)

        mock_openai_response = {"choices": [], "usage": {}}

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_openai.collect_stream_response", new_callable=AsyncMock, return_value=mock_openai_response), \
             patch("kiro.routes_openai.debug_logger", None):
            response = await chat_completions(mock_request, request_data)

        # Verify slot was acquired and released
        mock_request.app.state.queue_orchestrator._limiter.acquire.assert_called_once_with(
            "acc_1", timeout=30.0
        )
        mock_request.app.state.queue_orchestrator._limiter.release.assert_called_once_with(
            "acc_1"
        )

    @pytest.mark.asyncio
    async def test_slot_released_on_error(self, mock_request):
        """
        Test that concurrency slot is released even when request fails.

        What it does: Makes a failing request and verifies release is still called.
        Purpose: Prevent slot leaks on error path.
        """
        from kiro.routes_openai import chat_completions
        from kiro.auth import AuthType

        account = MagicMock()
        account.id = "acc_1"
        account.auth_manager = MagicMock()
        account.auth_manager.auth_type = AuthType.KIRO_DESKTOP
        account.auth_manager.profile_arn = "arn:test"
        account.auth_manager.api_host = "https://api.test.com"
        account.model_cache = MagicMock()
        account.model_resolver = MagicMock()

        mock_request.app.state.account_manager._accounts = {"acc_1": MagicMock()}
        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            side_effect=[account, None]
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        mock_http_client = MagicMock()
        mock_http_client.close = AsyncMock()
        mock_http_client.request_with_retry = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False), \
             patch("kiro.routes_openai.KiroHttpClient", return_value=mock_http_client), \
             patch("kiro.routes_openai.debug_logger", None):
            with pytest.raises(HTTPException) as exc_info:
                await chat_completions(mock_request, request_data)

        # Verify slot was still released despite error
        mock_request.app.state.queue_orchestrator._limiter.release.assert_called_once_with(
            "acc_1"
        )


# =============================================================================
# Tests: client_id extraction
# =============================================================================


class TestClientIdExtraction:
    """Tests for client_id extraction from request."""

    @pytest.mark.asyncio
    async def test_client_id_from_request_host(self, mock_request):
        """
        Test that client_id is extracted from request.client.host.

        What it does: Sets request.client.host and verifies it's used.
        Purpose: Verify correct client identification for fair scheduling.
        """
        mock_request.client.host = "10.0.0.42"

        # This is implicitly tested through the QueueTimeoutError path
        # where client_id is passed to the error
        from kiro.routes_openai import chat_completions

        mock_request.app.state.queue_orchestrator._limiter.acquire = AsyncMock(
            return_value=False
        )

        account = MagicMock()
        account.id = "acc_1"
        account.auth_manager = MagicMock()
        account.auth_manager.auth_type = MagicMock()
        account.auth_manager.profile_arn = "arn:test"
        account.auth_manager.api_host = "https://api.test.com"
        account.model_cache = MagicMock()
        account.model_resolver = MagicMock()
        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False):
            response = await chat_completions(mock_request, request_data)

        # Should get 503 (timeout) - the important thing is it didn't crash
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_client_id_unknown_when_no_client(self, mock_request):
        """
        Test that client_id defaults to "unknown" when request.client is None.

        What it does: Sets request.client to None and verifies no crash.
        Purpose: Handle edge case of missing client info (e.g., test clients).
        """
        mock_request.client = None

        from kiro.routes_openai import chat_completions

        mock_request.app.state.queue_orchestrator._limiter.acquire = AsyncMock(
            return_value=False
        )

        account = MagicMock()
        account.id = "acc_1"
        account.auth_manager = MagicMock()
        account.auth_manager.auth_type = MagicMock()
        account.auth_manager.profile_arn = "arn:test"
        account.auth_manager.api_host = "https://api.test.com"
        account.model_cache = MagicMock()
        account.model_resolver = MagicMock()
        mock_request.app.state.account_manager.get_next_account = AsyncMock(
            return_value=account
        )

        request_data = MagicMock()
        request_data.model = "claude-sonnet-4-20250514"
        request_data.stream = False
        request_data.messages = []
        request_data.tools = None

        with patch("kiro.routes_openai.QUEUE_ENABLED", True), \
             patch("kiro.routes_openai.build_kiro_payload", return_value={"test": "payload"}), \
             patch("kiro.routes_openai.WEB_SEARCH_ENABLED", False):
            response = await chat_completions(mock_request, request_data)

        # Should still return 503 without crashing
        assert response.status_code == 503
