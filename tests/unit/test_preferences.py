# -*- coding: utf-8 -*-

"""
Unit tests for kiro/preferences.py.

Tests Kiro/Amazon Q user preference operations, including overage enablement.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from kiro.auth import AuthType, KiroAuthManager
from kiro.preferences import (
    OVERAGE_STATUS_ENABLED,
    SET_USER_PREFERENCE_PATH,
    build_overage_preference_payload,
    enable_overage,
    should_enable_overage_for_auth,
)
from kiro.utils import KIRO_USER_AGENT, KIRO_X_AMZ_USER_AGENT


def _build_auth_manager(profile_arn: str = "arn:aws:codewhisperer:us-east-1:123456789:profile/test") -> KiroAuthManager:
    """
    Build a valid Kiro Desktop auth manager for preference tests.

    Args:
        profile_arn: Profile ARN to attach to the auth manager.

    Returns:
        KiroAuthManager with a non-expiring access token.
    """
    manager = KiroAuthManager(
        refresh_token="test_refresh_token",
        profile_arn=profile_arn,
        region="us-east-1",
    )
    manager._access_token = "test_access_token"
    manager._expires_at = datetime.now(timezone.utc).replace(year=2099)
    return manager


class TestBuildOveragePreferencePayload:
    """Tests for build_overage_preference_payload."""

    def test_build_payload_uses_enabled_status_and_profile_arn(self):
        """
        What it does: Builds the overage preference payload.
        Purpose: Ensure AWS receives the exact SetUserPreference JSON shape.
        """
        print("Setup: Creating profileArn...")
        profile_arn = "arn:aws:codewhisperer:us-east-1:123456789:profile/test"

        print("Action: Building overage preference payload...")
        payload = build_overage_preference_payload(profile_arn)

        print("Verification: Payload matches AWS SetUserPreference shape...")
        assert payload == {
            "overageConfiguration": {"overageStatus": OVERAGE_STATUS_ENABLED},
            "profileArn": profile_arn,
        }

    def test_empty_profile_arn_raises_value_error(self):
        """
        What it does: Attempts to build payload without profileArn.
        Purpose: Prevent malformed preference requests.
        """
        print("Action: Building payload with empty profileArn...")
        with pytest.raises(ValueError, match="profileArn is required"):
            build_overage_preference_payload("")


class TestShouldEnableOverageForAuth:
    """Tests for should_enable_overage_for_auth."""

    def test_returns_true_for_kiro_desktop_with_profile_arn(self):
        """
        What it does: Checks a normal Kiro Desktop auth manager.
        Purpose: Enable overage only when account metadata is available.
        """
        print("Setup: Creating Kiro Desktop auth manager with profileArn...")
        manager = _build_auth_manager()

        print("Verification: Overage can be enabled...")
        assert should_enable_overage_for_auth(manager) is True

    def test_returns_false_without_profile_arn(self):
        """
        What it does: Checks a Kiro Desktop auth manager without profileArn.
        Purpose: Avoid sending invalid SetUserPreference payloads.
        """
        print("Setup: Creating Kiro Desktop auth manager without profileArn...")
        manager = _build_auth_manager(profile_arn="")

        print("Verification: Overage cannot be enabled...")
        assert should_enable_overage_for_auth(manager) is False

    def test_returns_false_for_aws_sso_oidc(self):
        """
        What it does: Checks AWS SSO OIDC auth even if profileArn is present.
        Purpose: Preserve existing gateway rule that profileArn is Desktop-only.
        """
        print("Setup: Creating auth manager and forcing AWS SSO OIDC type...")
        manager = _build_auth_manager()
        manager._auth_type = AuthType.AWS_SSO_OIDC

        print("Verification: Overage is skipped for AWS SSO OIDC...")
        assert should_enable_overage_for_auth(manager) is False


class TestEnableOverage:
    """Tests for enable_overage."""

    @pytest.mark.asyncio
    async def test_enable_overage_posts_set_user_preference_request(self):
        """
        What it does: Enables overage with a mocked shared HTTP client.
        Purpose: Verify URL, headers, method, and request body exactly.
        """
        print("Setup: Creating auth manager and mocked HTTP response...")
        manager = _build_auth_manager()
        captured_request = {}

        async def request(method, url, **kwargs):
            captured_request["method"] = method
            captured_request["url"] = url
            captured_request["kwargs"] = kwargs
            return httpx.Response(status_code=200, json={})

        shared_client = Mock()
        shared_client.is_closed = False
        shared_client.request = AsyncMock(side_effect=request)

        print("Action: Enabling overage...")
        result = await enable_overage(manager, shared_client=shared_client)

        print("Verification: Request succeeded and matched SetUserPreference contract...")
        assert result is True
        assert captured_request["method"] == "POST"
        assert captured_request["url"] == f"{manager.q_host}{SET_USER_PREFERENCE_PATH}"
        assert captured_request["kwargs"]["json"] == {
            "overageConfiguration": {"overageStatus": "ENABLED"},
            "profileArn": manager.profile_arn,
        }
        headers = captured_request["kwargs"]["headers"]
        assert headers["Authorization"] == "Bearer test_access_token"
        assert headers["Content-Type"] == "application/x-amz-json-1.0"
        assert headers["x-amz-target"] == "AmazonCodeWhispererService.SetUserPreference"
        assert headers["User-Agent"] == KIRO_USER_AGENT
        assert headers["x-amz-user-agent"] == KIRO_X_AMZ_USER_AGENT

    @pytest.mark.asyncio
    async def test_enable_overage_returns_false_when_profile_arn_missing(self):
        """
        What it does: Calls enable_overage without profileArn.
        Purpose: Ensure missing profileArn skips network calls safely.
        """
        print("Setup: Creating auth manager without profileArn...")
        manager = _build_auth_manager(profile_arn="")
        shared_client = Mock()
        shared_client.is_closed = False
        shared_client.request = AsyncMock()

        print("Action: Enabling overage...")
        result = await enable_overage(manager, shared_client=shared_client)

        print("Verification: No network call was made...")
        assert result is False
        shared_client.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_enable_overage_returns_false_for_http_error(self):
        """
        What it does: Receives a non-200 SetUserPreference response.
        Purpose: Surface failure as False while keeping account initialization non-fatal.
        """
        print("Setup: Creating auth manager and HTTP 400 response...")
        manager = _build_auth_manager()
        shared_client = Mock()
        shared_client.is_closed = False
        shared_client.request = AsyncMock(
            return_value=httpx.Response(status_code=400, text='{"message":"bad request"}')
        )

        print("Action: Enabling overage...")
        result = await enable_overage(manager, shared_client=shared_client)

        print("Verification: Failure returns False...")
        assert result is False
        shared_client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_enable_overage_refreshes_token_on_403_then_retries(self):
        """
        What it does: First response is 403, second response is 200.
        Purpose: Preserve KiroHttpClient token-refresh retry behavior for preferences.
        """
        print("Setup: Creating auth manager with mocked force refresh...")
        manager = _build_auth_manager()
        manager.force_refresh = AsyncMock(return_value="refreshed_access_token")
        responses = [httpx.Response(status_code=403), httpx.Response(status_code=200, json={})]
        shared_client = Mock()
        shared_client.is_closed = False
        shared_client.request = AsyncMock(side_effect=responses)

        print("Action: Enabling overage...")
        result = await enable_overage(manager, shared_client=shared_client)

        print("Verification: Token refresh and retry occurred...")
        assert result is True
        assert shared_client.request.call_count == 2
        manager.force_refresh.assert_called_once()
