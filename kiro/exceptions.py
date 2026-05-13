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
Exception handlers for Kiro Gateway.

Contains custom exception classes and functions for handling validation errors
and other exceptions in a JSON-serialization compatible format.
"""

from typing import Any, List, Dict

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger


class QueueTimeoutError(Exception):
    """
    Raised when a request exceeds the maximum wait time in the queue.

    The request was accepted into the queue but no concurrency slot became
    available before QUEUE_TIMEOUT_SECONDS elapsed. The caller should return
    HTTP 503 to the client.

    Attributes:
        wait_time: Actual seconds the request waited before timing out.
        request_id: Identifier of the timed-out request.

    Example:
        >>> raise QueueTimeoutError(wait_time=30.1, request_id="abc123")
    """

    def __init__(self, wait_time: float = 0.0, request_id: str = "") -> None:
        """
        Initialize QueueTimeoutError.

        Args:
            wait_time: Seconds the request waited in the queue.
            request_id: Identifier of the timed-out request.
        """
        self.wait_time = wait_time
        self.request_id = request_id
        super().__init__(
            f"Request '{request_id}' timed out after {wait_time:.1f}s in queue"
        )


class QueueFullError(Exception):
    """
    Raised when the queue has reached its maximum capacity and cannot accept new requests.

    The request was rejected immediately without entering the queue because
    the number of pending requests has reached QUEUE_MAX_SIZE. The caller
    should return HTTP 503 to the client.

    Attributes:
        queue_size: Current number of pending requests in the queue.
        max_size: Maximum allowed queue size.

    Example:
        >>> raise QueueFullError(queue_size=50, max_size=50)
    """

    def __init__(self, queue_size: int = 0, max_size: int = 0) -> None:
        """
        Initialize QueueFullError.

        Args:
            queue_size: Current number of pending requests.
            max_size: Maximum allowed queue size.
        """
        self.queue_size = queue_size
        self.max_size = max_size
        super().__init__(
            f"Queue is full ({queue_size}/{max_size}), request rejected"
        )


class AccountRateLimited(Exception):
    """
    Raised when an account receives HTTP 429 and should not be retried on the same account.

    This exception signals to the caller (e.g., account manager or route handler)
    that the current account is rate-limited and a different account should be used
    instead of retrying the same one.

    Attributes:
        account_id: Identifier of the rate-limited account (empty string if unknown).
        retry_after: Seconds to wait before retrying, parsed from Retry-After header
                     (0.0 if header was absent or unparseable).

    Example:
        >>> raise AccountRateLimited(account_id="acc_123", retry_after=5.0)
        >>> # caller catches this and routes to a different account
    """

    def __init__(self, account_id: str = "", retry_after: float = 0.0) -> None:
        """
        Initialize AccountRateLimited exception.

        Args:
            account_id: Identifier of the rate-limited account.
            retry_after: Seconds to wait before the account can be used again.
        """
        self.account_id = account_id
        self.retry_after = retry_after
        super().__init__(f"Account {account_id} rate limited (retry_after={retry_after}s)")


def sanitize_validation_errors(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converts validation errors to JSON-serializable format.
    
    Pydantic may include bytes objects in the 'input' field, which
    are not JSON-serializable. This function converts them to strings.
    
    Args:
        errors: List of validation errors from Pydantic
    
    Returns:
        List of errors with bytes converted to strings
    """
    sanitized = []
    for error in errors:
        sanitized_error = {}
        for key, value in error.items():
            if isinstance(value, bytes):
                # Convert bytes to string
                sanitized_error[key] = value.decode("utf-8", errors="replace")
            elif isinstance(value, (list, tuple)):
                # Recursively process lists
                sanitized_error[key] = [
                    v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                    for v in value
                ]
            else:
                sanitized_error[key] = value
        sanitized.append(sanitized_error)
    return sanitized


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Pydantic validation error handler.
    
    Logs error details and returns an informative response.
    Correctly handles bytes objects in errors by converting them to strings.
    Also flushes debug logs for validation errors when DEBUG_MODE is enabled.
    
    Args:
        request: FastAPI Request object
        exc: Validation exception from Pydantic
    
    Returns:
        JSONResponse with error details and status 422
    """
    body = await request.body()
    body_str = body.decode("utf-8", errors="replace")
    
    # Sanitize errors for JSON serialization
    sanitized_errors = sanitize_validation_errors(exc.errors())
    
    logger.error(f"Validation error (422): {sanitized_errors}")
    # Log body at DEBUG level to avoid cluttering console with potentially large payloads
    # logger.debug(f"Request body: {body_str[:500]}...")
    
    # Flush debug logs for validation errors
    # This is called AFTER middleware has initialized debug logging,
    # so all app logs during request processing will be captured
    try:
        from kiro.debug_logger import debug_logger
        if debug_logger:
            error_message = f"Validation error: {sanitized_errors}"
            debug_logger.flush_on_error(422, error_message)
    except ImportError:
        pass  # debug_logger not available
    
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors, "body": body_str[:500]},
    )