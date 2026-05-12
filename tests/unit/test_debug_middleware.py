# -*- coding: utf-8 -*-

"""
Unit tests for DebugLoggerMiddleware (pure ASGI implementation).
Tests debug logging initialization at the middleware level.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_http_scope(path: str = "/v1/chat/completions") -> dict:
    """Create a minimal ASGI HTTP scope."""
    return {
        "type": "http",
        "path": path,
        "method": "POST",
        "headers": [],
    }


def _make_receive(body: bytes = b'{"model": "test"}') -> AsyncMock:
    """Create a receive callable that returns body then signals completion."""
    messages = [
        {"type": "http.request", "body": body, "more_body": False},
    ]
    receive = AsyncMock(side_effect=messages)
    return receive


def _make_send() -> AsyncMock:
    """Create a send callable that records sent messages."""
    return AsyncMock()


class TestDebugLoggerMiddlewareEndpointFiltering:
    """Tests for endpoint filtering in middleware."""

    @pytest.mark.asyncio
    async def test_skips_health_endpoint(self):
        """
        What it does: Verifies that middleware skips /health endpoint.
        Purpose: Ensure health checks are not logged.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/health")
            receive = _make_receive()
            send = _make_send()

            await middleware(scope, receive, send)

            # App should be called directly (passthrough)
            mock_app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_skips_docs_endpoint(self):
        """
        What it does: Verifies that middleware skips /docs endpoint.
        Purpose: Ensure documentation is not logged.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/docs")
            receive = _make_receive()
            send = _make_send()

            await middleware(scope, receive, send)

            mock_app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_skips_root_endpoint(self):
        """
        What it does: Verifies that middleware skips / endpoint.
        Purpose: Ensure root health check is not logged.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/")
            receive = _make_receive()
            send = _make_send()

            await middleware(scope, receive, send)

            mock_app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_processes_chat_completions_endpoint(self):
        """
        What it does: Verifies that middleware processes /v1/chat/completions.
        Purpose: Ensure OpenAI endpoint is logged.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/chat/completions")
            receive = _make_receive(b'{"model": "test"}')
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_called_once()
                # App is called with wrapped receive (not the original)
                mock_app.assert_called_once()
                call_args = mock_app.call_args
                assert call_args[0][0] == scope  # scope unchanged
                assert call_args[0][2] == send   # send unchanged

    @pytest.mark.asyncio
    async def test_processes_messages_endpoint(self):
        """
        What it does: Verifies that middleware processes /v1/messages.
        Purpose: Ensure Anthropic endpoint is logged.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/messages")
            receive = _make_receive(b'{"model": "claude"}')
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_called_once()


class TestDebugLoggerMiddlewareModeHandling:
    """Tests for DEBUG_MODE handling in middleware."""

    @pytest.mark.asyncio
    async def test_skips_when_debug_mode_off(self):
        """
        What it does: Verifies that middleware skips requests when DEBUG_MODE=off.
        Purpose: Ensure logging is disabled in off mode.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'off'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/chat/completions")
            receive = _make_receive()
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_not_called()
                # App called directly (passthrough)
                mock_app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_processes_when_debug_mode_errors(self):
        """
        What it does: Verifies that middleware works when DEBUG_MODE=errors.
        Purpose: Ensure errors mode activates logging.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'errors'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/chat/completions")
            receive = _make_receive(b'{"test": "data"}')
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_when_debug_mode_all(self):
        """
        What it does: Verifies that middleware works when DEBUG_MODE=all.
        Purpose: Ensure all mode activates logging.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/messages")
            receive = _make_receive(b'{"test": "data"}')
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_called_once()


class TestDebugLoggerMiddlewareErrorHandling:
    """Tests for error handling in middleware."""

    @pytest.mark.asyncio
    async def test_handles_body_log_error_gracefully(self):
        """
        What it does: Verifies that middleware handles log errors gracefully.
        Purpose: Ensure log errors don't break the request.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/chat/completions")
            receive = _make_receive(b'{"model": "test"}')
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                mock_logger.log_request_body.side_effect = Exception("Log write error")

                # Should not raise - error is caught internally
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_called_once()
                # App should still be called despite log error
                mock_app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_empty_body(self):
        """
        What it does: Verifies that middleware doesn't log empty body.
        Purpose: Ensure empty requests don't create unnecessary logs.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/chat/completions")
            receive = _make_receive(b'')  # Empty body
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger') as mock_logger:
                await middleware(scope, receive, send)

                mock_logger.prepare_new_request.assert_called_once()
                # Empty body should not trigger log_request_body
                mock_logger.log_request_body.assert_not_called()


class TestDebugLoggerMiddlewareResponsePassthrough:
    """Tests for transparent response passthrough."""

    @pytest.mark.asyncio
    async def test_passes_through_to_app(self):
        """
        What it does: Verifies that middleware passes scope/send to app unchanged.
        Purpose: Ensure middleware doesn't modify the response path.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = _make_http_scope("/v1/chat/completions")
            receive = _make_receive(b'{"test": "data"}')
            send = _make_send()

            with patch('kiro.debug_logger.debug_logger'):
                await middleware(scope, receive, send)

                # Verify app was called with correct scope and send
                call_args = mock_app.call_args[0]
                assert call_args[0] == scope
                assert call_args[2] == send

    @pytest.mark.asyncio
    async def test_websocket_passthrough(self):
        """
        What it does: Verifies that non-HTTP scopes pass through directly.
        Purpose: Ensure WebSocket and other protocols are not intercepted.
        """
        with patch('kiro.debug_middleware.DEBUG_MODE', 'all'):
            from kiro.debug_middleware import DebugLoggerMiddleware

            mock_app = AsyncMock()
            middleware = DebugLoggerMiddleware(app=mock_app)

            scope = {"type": "websocket", "path": "/ws"}
            receive = _make_receive()
            send = _make_send()

            await middleware(scope, receive, send)

            # Should pass through directly without any interception
            mock_app.assert_called_once_with(scope, receive, send)


class TestLoggedEndpointsConstant:
    """Tests for LOGGED_ENDPOINTS constant."""

    def test_logged_endpoints_contains_chat_completions(self):
        """
        What it does: Verifies that LOGGED_ENDPOINTS contains /v1/chat/completions.
        Purpose: Ensure OpenAI endpoint is included in logging.
        """
        from kiro.debug_middleware import LOGGED_ENDPOINTS
        assert "/v1/chat/completions" in LOGGED_ENDPOINTS

    def test_logged_endpoints_contains_messages(self):
        """
        What it does: Verifies that LOGGED_ENDPOINTS contains /v1/messages.
        Purpose: Ensure Anthropic endpoint is included in logging.
        """
        from kiro.debug_middleware import LOGGED_ENDPOINTS
        assert "/v1/messages" in LOGGED_ENDPOINTS

    def test_logged_endpoints_is_frozenset(self):
        """
        What it does: Verifies that LOGGED_ENDPOINTS is a frozenset.
        Purpose: Ensure O(1) lookup and immutability.
        """
        from kiro.debug_middleware import LOGGED_ENDPOINTS
        assert isinstance(LOGGED_ENDPOINTS, frozenset)
