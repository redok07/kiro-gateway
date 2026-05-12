# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
# Copyright (C) 2025 Jwadow
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
Debug logging middleware for Kiro Gateway (pure ASGI implementation).

This middleware initializes debug logging BEFORE Pydantic validation,
which allows capturing validation errors (422) in debug logs.

Uses a pure ASGI middleware instead of Starlette's BaseHTTPMiddleware to avoid
response body buffering that breaks true streaming and adds latency.

The middleware:
1. Intercepts requests to API endpoints (/v1/chat/completions, /v1/messages)
2. Calls prepare_new_request() to initialize buffers and loguru sink
3. Reads and logs the raw request body
4. Passes the request to the next handler

Flush/discard operations are handled by:
- Route handlers (for successful requests and Kiro API errors)
- Exception handlers (for validation errors and other exceptions)
"""

from starlette.types import ASGIApp, Receive, Scope, Send
from loguru import logger

from kiro.config import DEBUG_MODE


# API endpoints that should have debug logging enabled
# These are the main API endpoints that process user requests
LOGGED_ENDPOINTS = frozenset({
    "/v1/chat/completions",  # OpenAI-compatible endpoint
    "/v1/messages",          # Anthropic-compatible endpoint
})


class DebugLoggerMiddleware:
    """
    Pure ASGI middleware for initializing debug logging on API requests.
    
    This middleware runs BEFORE Pydantic validation, which means it can
    capture the raw request body even for requests that fail validation.
    
    Unlike BaseHTTPMiddleware, this does NOT buffer the response body,
    preserving true streaming behavior and reducing latency.
    
    The middleware only activates for API endpoints defined in LOGGED_ENDPOINTS.
    Health checks, documentation, and other endpoints are not logged.
    
    Lifecycle:
    - prepare_new_request(): Called here (before validation)
    - log_request_body(): Called here (raw body from client)
    - log_kiro_request_body(): Called in route handlers (transformed payload)
    - flush_on_error() / discard_buffers(): Called in routes or exception handlers
    """
    
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ASGI interface. Only intercepts HTTP requests to logged endpoints.
        
        Args:
            scope: ASGI connection scope
            receive: ASGI receive callable
            send: ASGI send callable
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        path = scope.get("path", "")
        
        # Skip logging for non-API endpoints (health, docs, etc.)
        if path not in LOGGED_ENDPOINTS:
            await self.app(scope, receive, send)
            return
        
        # Skip if debug mode is disabled
        if DEBUG_MODE == "off":
            await self.app(scope, receive, send)
            return
        
        # Import here to avoid circular imports and allow graceful degradation
        try:
            from kiro.debug_logger import debug_logger
        except ImportError:
            logger.warning("debug_logger not available, skipping debug logging")
            await self.app(scope, receive, send)
            return
        
        # Initialize debug logging for this request
        debug_logger.prepare_new_request()
        
        # Intercept the request body for logging without consuming it
        body_chunks: list[bytes] = []
        body_complete = False
        
        async def receive_wrapper() -> dict:
            nonlocal body_complete
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if body:
                    body_chunks.append(body)
                if not message.get("more_body", False):
                    body_complete = True
                    # Log the complete request body
                    full_body = b"".join(body_chunks)
                    if full_body:
                        try:
                            debug_logger.log_request_body(full_body)
                        except Exception as e:
                            logger.warning(f"Failed to log request body: {e}")
            return message
        
        await self.app(scope, receive_wrapper, send)
