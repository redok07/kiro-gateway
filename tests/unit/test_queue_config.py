# -*- coding: utf-8 -*-

"""
Tests for kiro/queue_config.py.

Verifies default values, env var overrides, and type correctness
for all queue system configuration constants.
"""

import os
import importlib
import pytest


def reload_queue_config():
    """Reload queue_config module to pick up env var changes."""
    import kiro.queue_config as qc
    importlib.reload(qc)
    return qc


class TestQueueConfigDefaults:
    """Test default values when no env vars are set."""

    def setup_method(self):
        """Remove any queue-related env vars before each test."""
        env_keys = [
            "MAX_CONCURRENT_PER_ACCOUNT",
            "QUEUE_MAX_SIZE",
            "QUEUE_TIMEOUT_SECONDS",
            "RETRY_429_ON_SAME_ACCOUNT",
            "RETRY_5XX_ON_SAME_ACCOUNT",
            "FAST_429_RECOVERY_SECONDS",
            "QUEUE_ENABLED",
        ]
        for key in env_keys:
            os.environ.pop(key, None)

    def test_max_concurrent_per_account_default(self):
        """MAX_CONCURRENT_PER_ACCOUNT defaults to 2."""
        qc = reload_queue_config()
        assert qc.MAX_CONCURRENT_PER_ACCOUNT == 2
        assert isinstance(qc.MAX_CONCURRENT_PER_ACCOUNT, int)

    def test_queue_max_size_default(self):
        """QUEUE_MAX_SIZE defaults to 50."""
        qc = reload_queue_config()
        assert qc.QUEUE_MAX_SIZE == 50
        assert isinstance(qc.QUEUE_MAX_SIZE, int)

    def test_queue_timeout_seconds_default(self):
        """QUEUE_TIMEOUT_SECONDS defaults to 30.0."""
        qc = reload_queue_config()
        assert qc.QUEUE_TIMEOUT_SECONDS == 30.0
        assert isinstance(qc.QUEUE_TIMEOUT_SECONDS, float)

    def test_retry_429_on_same_account_default(self):
        """RETRY_429_ON_SAME_ACCOUNT defaults to 0."""
        qc = reload_queue_config()
        assert qc.RETRY_429_ON_SAME_ACCOUNT == 0
        assert isinstance(qc.RETRY_429_ON_SAME_ACCOUNT, int)

    def test_retry_5xx_on_same_account_default(self):
        """RETRY_5XX_ON_SAME_ACCOUNT defaults to 2."""
        qc = reload_queue_config()
        assert qc.RETRY_5XX_ON_SAME_ACCOUNT == 2
        assert isinstance(qc.RETRY_5XX_ON_SAME_ACCOUNT, int)

    def test_fast_429_recovery_seconds_default(self):
        """FAST_429_RECOVERY_SECONDS defaults to 5.0."""
        qc = reload_queue_config()
        assert qc.FAST_429_RECOVERY_SECONDS == 5.0
        assert isinstance(qc.FAST_429_RECOVERY_SECONDS, float)

    def test_queue_enabled_default(self):
        """QUEUE_ENABLED defaults to True."""
        qc = reload_queue_config()
        assert qc.QUEUE_ENABLED is True
        assert isinstance(qc.QUEUE_ENABLED, bool)


class TestQueueConfigEnvOverrides:
    """Test that env vars correctly override defaults."""

    def setup_method(self):
        """Remove any queue-related env vars before each test."""
        env_keys = [
            "MAX_CONCURRENT_PER_ACCOUNT",
            "QUEUE_MAX_SIZE",
            "QUEUE_TIMEOUT_SECONDS",
            "RETRY_429_ON_SAME_ACCOUNT",
            "RETRY_5XX_ON_SAME_ACCOUNT",
            "FAST_429_RECOVERY_SECONDS",
            "QUEUE_ENABLED",
        ]
        for key in env_keys:
            os.environ.pop(key, None)

    def teardown_method(self):
        """Clean up env vars after each test."""
        env_keys = [
            "MAX_CONCURRENT_PER_ACCOUNT",
            "QUEUE_MAX_SIZE",
            "QUEUE_TIMEOUT_SECONDS",
            "RETRY_429_ON_SAME_ACCOUNT",
            "RETRY_5XX_ON_SAME_ACCOUNT",
            "FAST_429_RECOVERY_SECONDS",
            "QUEUE_ENABLED",
        ]
        for key in env_keys:
            os.environ.pop(key, None)

    def test_max_concurrent_per_account_env_override(self):
        """MAX_CONCURRENT_PER_ACCOUNT reads from env var."""
        os.environ["MAX_CONCURRENT_PER_ACCOUNT"] = "5"
        qc = reload_queue_config()
        assert qc.MAX_CONCURRENT_PER_ACCOUNT == 5
        assert isinstance(qc.MAX_CONCURRENT_PER_ACCOUNT, int)

    def test_queue_max_size_env_override(self):
        """QUEUE_MAX_SIZE reads from env var."""
        os.environ["QUEUE_MAX_SIZE"] = "100"
        qc = reload_queue_config()
        assert qc.QUEUE_MAX_SIZE == 100
        assert isinstance(qc.QUEUE_MAX_SIZE, int)

    def test_queue_timeout_seconds_env_override(self):
        """QUEUE_TIMEOUT_SECONDS reads from env var as float."""
        os.environ["QUEUE_TIMEOUT_SECONDS"] = "60.5"
        qc = reload_queue_config()
        assert qc.QUEUE_TIMEOUT_SECONDS == 60.5
        assert isinstance(qc.QUEUE_TIMEOUT_SECONDS, float)

    def test_retry_429_on_same_account_env_override(self):
        """RETRY_429_ON_SAME_ACCOUNT reads from env var."""
        os.environ["RETRY_429_ON_SAME_ACCOUNT"] = "3"
        qc = reload_queue_config()
        assert qc.RETRY_429_ON_SAME_ACCOUNT == 3
        assert isinstance(qc.RETRY_429_ON_SAME_ACCOUNT, int)

    def test_retry_5xx_on_same_account_env_override(self):
        """RETRY_5XX_ON_SAME_ACCOUNT reads from env var."""
        os.environ["RETRY_5XX_ON_SAME_ACCOUNT"] = "4"
        qc = reload_queue_config()
        assert qc.RETRY_5XX_ON_SAME_ACCOUNT == 4
        assert isinstance(qc.RETRY_5XX_ON_SAME_ACCOUNT, int)

    def test_fast_429_recovery_seconds_env_override(self):
        """FAST_429_RECOVERY_SECONDS reads from env var as float."""
        os.environ["FAST_429_RECOVERY_SECONDS"] = "10.0"
        qc = reload_queue_config()
        assert qc.FAST_429_RECOVERY_SECONDS == 10.0
        assert isinstance(qc.FAST_429_RECOVERY_SECONDS, float)

    def test_queue_enabled_false_via_env(self):
        """QUEUE_ENABLED can be disabled via env var."""
        os.environ["QUEUE_ENABLED"] = "false"
        qc = reload_queue_config()
        assert qc.QUEUE_ENABLED is False

    def test_queue_enabled_true_via_1(self):
        """QUEUE_ENABLED accepts '1' as truthy."""
        os.environ["QUEUE_ENABLED"] = "1"
        qc = reload_queue_config()
        assert qc.QUEUE_ENABLED is True

    def test_queue_enabled_true_via_yes(self):
        """QUEUE_ENABLED accepts 'yes' as truthy."""
        os.environ["QUEUE_ENABLED"] = "yes"
        qc = reload_queue_config()
        assert qc.QUEUE_ENABLED is True

    def test_queue_enabled_false_via_0(self):
        """QUEUE_ENABLED treats '0' as falsy."""
        os.environ["QUEUE_ENABLED"] = "0"
        qc = reload_queue_config()
        assert qc.QUEUE_ENABLED is False
