# -*- coding: utf-8 -*-

"""
Tests for HTTP 429 rate-limit handling in KiroHttpClient.

Verifies:
- 429 raises AccountRateLimited immediately when RETRY_429_ON_SAME_ACCOUNT=0
- Retry-After header is parsed and stored on the exception
- 5xx errors still retry normally (unchanged behavior)
- Timeouts still retry normally (unchanged behavior)
- RETRY_429_ON_SAME_ACCOUNT>0 retries on same account up to that limit
- 403 handling is unchanged
- AccountRateLimited exception attributes are correct
- Happy path (200) still works
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from kiro.exceptions import AccountRateLimited


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, headers: dict | None = None) -> MagicMock:
    """Build a minimal mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


def _make_client(mock_responses: list, auth_token: str = "test-token"):
    """
    Build a KiroHttpClient whose underlying httpx.AsyncClient returns
    mock_responses in sequence.

    Returns:
        Tuple of (KiroHttpClient instance, mock httpx client).
    """
    from kiro.http_client import KiroHttpClient

    auth_manager = MagicMock()
    auth_manager.get_access_token = AsyncMock(return_value=auth_token)
    auth_manager.force_refresh = AsyncMock()

    client = KiroHttpClient(auth_manager)

    # Plain MagicMock — no spec so httpx.AsyncClient.__init__ doesn't run
    mock_http = MagicMock()
    mock_http.is_closed = False
    mock_http.request = AsyncMock(side_effect=mock_responses)

    client._get_client = AsyncMock(return_value=mock_http)
    return client, mock_http


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestAccountRateLimitedException:
    """Unit tests for the AccountRateLimited exception class itself."""

    def test_account_rate_limited_exception_attributes(self):
        """Exception stores account_id and retry_after correctly."""
        exc = AccountRateLimited(account_id="acc_42", retry_after=7.5)
        assert exc.account_id == "acc_42"
        assert exc.retry_after == 7.5

    def test_account_rate_limited_default_attributes(self):
        """Exception has sensible defaults when no args provided."""
        exc = AccountRateLimited()
        assert exc.account_id == ""
        assert exc.retry_after == 0.0

    def test_account_rate_limited_str_representation(self):
        """Exception message includes account_id and retry_after."""
        exc = AccountRateLimited(account_id="acc_1", retry_after=5.0)
        msg = str(exc)
        assert "acc_1" in msg
        assert "5.0" in msg

    def test_account_rate_limited_is_exception(self):
        """AccountRateLimited is a proper Exception subclass."""
        exc = AccountRateLimited()
        assert isinstance(exc, Exception)


class TestHttp429ImmediateRaise:
    """429 raises AccountRateLimited immediately when RETRY_429_ON_SAME_ACCOUNT=0."""

    @pytest.mark.asyncio
    async def test_429_raises_account_rate_limited_immediately(self):
        """429 response raises AccountRateLimited without retrying."""
        client, mock_http = _make_client([_make_response(429)])

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            with pytest.raises(AccountRateLimited):
                await client.request_with_retry("POST", "https://example.com/api")

        # Only 1 request made — no retries
        assert mock_http.request.call_count == 1

    @pytest.mark.asyncio
    async def test_429_includes_retry_after_header(self):
        """Retry-After header value is parsed and stored on the exception."""
        resp = _make_response(429, headers={"Retry-After": "5"})
        client, _ = _make_client([resp])

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            with pytest.raises(AccountRateLimited) as exc_info:
                await client.request_with_retry("POST", "https://example.com/api")

        assert exc_info.value.retry_after == 5.0

    @pytest.mark.asyncio
    async def test_429_retry_after_missing_defaults_to_zero(self):
        """Missing Retry-After header results in retry_after=0.0."""
        resp = _make_response(429, headers={})
        client, _ = _make_client([resp])

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            with pytest.raises(AccountRateLimited) as exc_info:
                await client.request_with_retry("POST", "https://example.com/api")

        assert exc_info.value.retry_after == 0.0

    @pytest.mark.asyncio
    async def test_429_retry_after_invalid_value_defaults_to_zero(self):
        """Non-numeric Retry-After header results in retry_after=0.0."""
        resp = _make_response(429, headers={"Retry-After": "not-a-number"})
        client, _ = _make_client([resp])

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            with pytest.raises(AccountRateLimited) as exc_info:
                await client.request_with_retry("POST", "https://example.com/api")

        assert exc_info.value.retry_after == 0.0


class TestHttp429WithRetryConfig:
    """429 retries on same account when RETRY_429_ON_SAME_ACCOUNT > 0."""

    @pytest.mark.asyncio
    async def test_429_with_retry_config_nonzero_retries_then_raises(self):
        """With RETRY_429_ON_SAME_ACCOUNT=2, retries twice then raises AccountRateLimited."""
        responses = [
            _make_response(429),
            _make_response(429),
            _make_response(429),
        ]
        client, mock_http = _make_client(responses)

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 2):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(AccountRateLimited):
                    await client.request_with_retry("POST", "https://example.com/api")

        # 3 requests: attempt 0, attempt 1, attempt 2 (exhausted)
        assert mock_http.request.call_count == 3

    @pytest.mark.asyncio
    async def test_429_with_retry_config_succeeds_on_retry(self):
        """With RETRY_429_ON_SAME_ACCOUNT=2, succeeds if a retry returns 200."""
        responses = [
            _make_response(429),
            _make_response(200),
        ]
        client, mock_http = _make_client(responses)

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 2):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.request_with_retry("POST", "https://example.com/api")

        assert result.status_code == 200
        assert mock_http.request.call_count == 2


class TestNon429RetryBehaviorUnchanged:
    """Non-429 retry behavior must remain unchanged."""

    @pytest.mark.asyncio
    async def test_5xx_still_retries_normally(self):
        """500 errors retry with backoff; success on third attempt."""
        responses = [
            _make_response(500),
            _make_response(500),
            _make_response(200),
        ]
        client, mock_http = _make_client(responses)

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.request_with_retry("POST", "https://example.com/api")

        assert result.status_code == 200
        assert mock_http.request.call_count == 3

    @pytest.mark.asyncio
    async def test_timeout_still_retries(self):
        """Timeout errors retry; success on second attempt."""
        responses = [
            httpx.ReadTimeout("timed out"),
            _make_response(200),
        ]
        client, mock_http = _make_client(responses)

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.request_with_retry("POST", "https://example.com/api")

        assert result.status_code == 200
        assert mock_http.request.call_count == 2

    @pytest.mark.asyncio
    async def test_403_still_handled_normally(self):
        """403 triggers token refresh and retry; success on second attempt."""
        responses = [
            _make_response(403),
            _make_response(200),
        ]
        client, mock_http = _make_client(responses)

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            result = await client.request_with_retry("POST", "https://example.com/api")

        assert result.status_code == 200
        assert mock_http.request.call_count == 2
        # force_refresh was called once for the 403
        client.auth_manager.force_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_retry_logic_unchanged_for_200(self):
        """Happy path: 200 on first attempt returns immediately."""
        client, mock_http = _make_client([_make_response(200)])

        with patch("kiro.http_client.RETRY_429_ON_SAME_ACCOUNT", 0):
            result = await client.request_with_retry("POST", "https://example.com/api")

        assert result.status_code == 200
        assert mock_http.request.call_count == 1
