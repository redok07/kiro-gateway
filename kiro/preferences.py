# -*- coding: utf-8 -*-

# Kiro Gateway
# https://github.com/jwadow/kiro-gateway
# Copyright (C) 2025 Jwadow
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
Kiro user preference operations.

Contains helpers for AWS CodeWhisperer user preference APIs that are not part
of the OpenAI/Anthropic proxy surface but are needed for account preparation.
"""

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from kiro.auth import AuthType, KiroAuthManager
from kiro.config import KIRO_SET_USER_PREFERENCE_TARGET
from kiro.http_client import KiroHttpClient


OVERAGE_STATUS_ENABLED: str = "ENABLED"
SET_USER_PREFERENCE_PATH: str = "/setUserPreference"


def build_overage_preference_payload(profile_arn: str) -> Dict[str, Any]:
    """
    Build the SetUserPreference payload for enabling overage.

    Args:
        profile_arn: AWS CodeWhisperer profile ARN for the Kiro Desktop account.

    Returns:
        JSON payload accepted by AmazonCodeWhispererService.SetUserPreference.

    Raises:
        ValueError: If profile_arn is empty.
    """
    if not profile_arn:
        raise ValueError("profileArn is required to enable Kiro overage")

    return {
        "overageConfiguration": {
            "overageStatus": OVERAGE_STATUS_ENABLED,
        },
        "profileArn": profile_arn,
    }


def should_enable_overage_for_auth(auth_manager: KiroAuthManager) -> bool:
    """
    Return whether an auth manager has the account metadata required for overage.

    AWS SSO OIDC/Builder ID calls should not include profileArn in this gateway;
    existing request conversion follows the same rule to avoid upstream 403s.

    Args:
        auth_manager: Kiro authentication manager for an account.

    Returns:
        True when overage can be enabled safely for this account.
    """
    return auth_manager.auth_type == AuthType.KIRO_DESKTOP and bool(auth_manager.profile_arn)


def _set_user_preference_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """
    Adjust standard Kiro headers for the SetUserPreference JSON protocol.

    Args:
        headers: Headers produced by KiroHttpClient/get_kiro_headers.

    Returns:
        A copied header dict with AWS JSON protocol fields applied.
    """
    preference_headers = headers.copy()
    preference_headers["Content-Type"] = "application/x-amz-json-1.0"
    preference_headers["x-amz-target"] = KIRO_SET_USER_PREFERENCE_TARGET
    return preference_headers


async def enable_overage(
    auth_manager: KiroAuthManager,
    shared_client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """
    Enable Kiro/Amazon Q overage for a Kiro Desktop account.

    Sends AmazonCodeWhispererService.SetUserPreference with
    overageConfiguration.overageStatus set to ENABLED. The call is skipped for
    accounts that do not have a Kiro Desktop profileArn because existing Kiro
    API patterns treat profileArn as Desktop-only.

    Args:
        auth_manager: Authentication manager for the account to configure.
        shared_client: Optional shared HTTP client for connection pooling.

    Returns:
        True if AWS returned HTTP 200, False if skipped or rejected.
    """
    if not should_enable_overage_for_auth(auth_manager):
        logger.debug("Skipping overage enablement: Kiro Desktop profileArn is unavailable")
        return False

    profile_arn = auth_manager.profile_arn
    if not profile_arn:
        logger.debug("Skipping overage enablement: profileArn is empty")
        return False

    payload = build_overage_preference_payload(profile_arn)
    url = f"{auth_manager.q_host}{SET_USER_PREFERENCE_PATH}"
    http_client = KiroHttpClient(auth_manager, shared_client=shared_client)

    try:
        response = await http_client.request_with_retry(
            method="POST",
            url=url,
            json_data=payload,
            params=None,
            stream=False,
            headers_transform=_set_user_preference_headers,
        )
    finally:
        await http_client.close()

    if response.status_code == 200:
        logger.info("Kiro overage enabled for account profile")
        return True

    logger.warning(
        f"Failed to enable Kiro overage: HTTP {response.status_code} - {response.text[:500]}"
    )
    return False
