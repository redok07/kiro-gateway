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
Queue System Configuration.

Module-level constants for the request queue system, loaded from environment
variables with sensible defaults. All values are type-coerced at import time.

Environment variables:
    MAX_CONCURRENT_PER_ACCOUNT: Max simultaneous requests per account (int, default 2)
    QUEUE_MAX_SIZE: Max pending requests before rejecting new ones (int, default 50)
    QUEUE_TIMEOUT_SECONDS: Max wait time in queue before returning 503 (float, default 30.0)
    RETRY_429_ON_SAME_ACCOUNT: Retry count for 429 errors on same account (int, default 0)
    RETRY_5XX_ON_SAME_ACCOUNT: Retry count for 5xx errors on same account (int, default 2)
    FAST_429_RECOVERY_SECONDS: Shorter cooldown period after 429 errors (float, default 5.0)
    QUEUE_ENABLED: Feature flag to disable the queue entirely (bool, default true)
"""

import os

# ==================================================================================================
# Queue System Settings
# ==================================================================================================

# Maximum number of simultaneous requests allowed per account.
# Requests beyond this limit are held in the queue until a slot opens.
MAX_CONCURRENT_PER_ACCOUNT: int = int(os.getenv("MAX_CONCURRENT_PER_ACCOUNT", "1"))

# Maximum number of requests that can wait in the queue.
# When the queue is full, new requests are rejected with HTTP 503.
QUEUE_MAX_SIZE: int = int(os.getenv("QUEUE_MAX_SIZE", "50"))

# Maximum time in seconds a request may wait in the queue before timing out.
# Requests that exceed this wait time are rejected with HTTP 503.
QUEUE_TIMEOUT_SECONDS: float = float(os.getenv("QUEUE_TIMEOUT_SECONDS", "30.0"))

# Number of retries on the same account after a 429 (Too Many Requests) error.
# Default is 0: immediately route to a different account on 429 rather than retrying.
RETRY_429_ON_SAME_ACCOUNT: int = int(os.getenv("RETRY_429_ON_SAME_ACCOUNT", "0"))

# Number of retries on the same account after a 5xx (server error) response.
# Server errors are transient, so a small retry count is appropriate.
RETRY_5XX_ON_SAME_ACCOUNT: int = int(os.getenv("RETRY_5XX_ON_SAME_ACCOUNT", "2"))

# Cooldown period in seconds applied to an account after a 429 error.
# Shorter than the general Circuit Breaker timeout because 429 is a rate-limit
# signal rather than a hard failure — the account recovers quickly.
FAST_429_RECOVERY_SECONDS: float = float(os.getenv("FAST_429_RECOVERY_SECONDS", "5.0"))

# Feature flag: set to false/0/no to disable the queue entirely and fall back
# to direct request handling without concurrency limiting.
QUEUE_ENABLED: bool = os.getenv("QUEUE_ENABLED", "true").lower() in ("true", "1", "yes")
