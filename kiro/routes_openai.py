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
FastAPI routes for Kiro Gateway.

Contains all API endpoints:
- / and /health: Health check
- /v1/models: Models list
- /v1/chat/completions: Chat completions
"""

import json
import time
import ssl
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from loguru import logger

from kiro.config import (
    PROXY_API_KEY,
    APP_VERSION,
    ACCOUNTS_CONFIG_FILE,
    ACCOUNTS_STATE_FILE,
    PROFILE_ARN,
)
from kiro.models_openai import (
    OpenAIModel,
    ModelList,
    ChatCompletionRequest,
)
from kiro.auth import KiroAuthManager, AuthType
from kiro.cache import ModelInfoCache
from kiro.model_resolver import ModelResolver
from kiro.converters_openai import build_kiro_payload
from kiro.streaming_openai import stream_kiro_to_openai, collect_stream_response, stream_with_first_token_retry
from kiro.http_client import KiroHttpClient
from kiro.utils import KIRO_USER_AGENT, KIRO_X_AMZ_USER_AGENT, generate_conversation_id
from kiro.config import WEB_SEARCH_ENABLED
from kiro.mcp_tools import handle_native_web_search
from kiro.queue_config import QUEUE_ENABLED
from kiro.exceptions import AccountRateLimited, QueueTimeoutError, QueueFullError

# Import debug_logger
try:
    from kiro.debug_logger import debug_logger
except ImportError:
    debug_logger = None


# --- Security scheme ---
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def verify_api_key(auth_header: str = Security(api_key_header)) -> bool:
    """
    Verify API key in Authorization header.
    
    Expects format: "Bearer {PROXY_API_KEY}"
    
    Args:
        auth_header: Authorization header value
    
    Returns:
        True if key is valid
    
    Raises:
        HTTPException: 401 if key is invalid or missing
    """
    if not auth_header or auth_header != f"Bearer {PROXY_API_KEY}":
        logger.warning("Access attempt with invalid API key.")
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return True


# --- Router ---
router = APIRouter()


@router.get("/")
async def root():
    """
    Health check endpoint.
    
    Returns:
        Status and application version
    """
    return {
        "status": "ok",
        "message": "Kiro Gateway is running",
        "version": APP_VERSION
    }


@router.get("/health")
async def health():
    """
    Detailed health check.
    
    Returns:
        Status, timestamp and version
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION
    }

@router.get("/accounts/quota")
async def accounts_quota(
    request: Request,
    key: str = Query(None, description="API key for authentication"),
    refresh: bool = Query(False, description="Live fetch quotas from Kiro API"),
):
    """
    Account quota information endpoint.

    Returns quota status for all configured accounts. Accessible via query param auth.

    Args:
        request: FastAPI Request for accessing app.state
        key: PROXY_API_KEY passed as query parameter
        refresh: If true, fetches live quota from Kiro API (slower)

    Returns:
        JSON with account quota details, status, and summary
    """
    if not key or key != PROXY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing key parameter")

    account_manager = request.app.state.account_manager
    accounts_dict = account_manager._accounts

    # Build account list from account_manager state
    account_list = []
    total_remaining = 0
    total_limit = 0
    active_count = 0
    disabled_count = 0

    # Load cached quota from state.json
    quota_cache = _load_quota_cache_for_endpoint()

    # If refresh requested, fetch live quotas
    if refresh:
        quota_cache = await _fetch_live_quotas()

    for account_id, account in accounts_dict.items():
        entry = {
            "id": account_id,
            "status": "disabled" if account.disabled else "active",
            "failures": account.failures,
            "total_requests": account.stats.total_requests,
            "successful_requests": account.stats.successful_requests,
            "failed_requests": account.stats.failed_requests,
        }

        if account.disabled:
            entry["disabled_reason"] = account.disabled_reason
            entry["disabled_at"] = datetime.fromtimestamp(
                account.disabled_at, tz=timezone.utc
            ).isoformat() if account.disabled_at else None
            disabled_count += 1
        else:
            active_count += 1

        # Attach quota info from cache
        cached = quota_cache.get(account_id)
        if cached:
            entry["package"] = cached.get("packageName", "Unknown")
            entry["used"] = cached.get("usedCredits", 0)
            entry["limit"] = cached.get("totalCredits", 0)
            entry["remaining"] = cached.get("remainingCredits", 0)
            entry["last_checked"] = cached.get("lastFetched")
            total_remaining += entry["remaining"]
            total_limit += entry["limit"]

        account_list.append(entry)

    return {
        "total_accounts": len(accounts_dict),
        "active_accounts": active_count,
        "disabled_accounts": disabled_count,
        "accounts": account_list,
        "summary": {
            "total_remaining": total_remaining,
            "total_limit": total_limit,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _load_quota_cache_for_endpoint() -> dict:
    """Load cached quota data from state.json for the endpoint.

    Returns:
        Dict mapping account_id to quota info.
    """
    state_path = Path(ACCOUNTS_STATE_FILE)
    if not state_path.exists():
        return {}

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        return state_data.get("quota_cache", {})
    except Exception:
        return {}


async def _fetch_live_quotas() -> dict:
    """Fetch live quota from Kiro API for all accounts.

    Reads credential files from ACCOUNTS_CONFIG_FILE, fetches quota in parallel,
    updates state.json quota_cache, and returns the cache dict.

    Returns:
        Dict mapping account path to quota info.
    """
    import httpx

    KIRO_USAGE_ENDPOINT = "https://q.us-east-1.amazonaws.com/getUsageLimits"

    config_path = Path(ACCOUNTS_CONFIG_FILE)
    if not config_path.exists():
        logger.warning(f"Credentials file not found: {config_path}")
        return {}

    try:
        accounts = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(accounts, list) or not accounts:
            return {}
    except Exception as e:
        logger.error(f"Error reading credentials file: {e}")
        return {}

    async def fetch_single(client: httpx.AsyncClient, entry: dict) -> tuple:
        """Fetch quota for a single account. Returns (path, result_dict)."""
        entry_path = entry.get("path", "")
        if not entry_path:
            return (entry_path, None)

        cred_path = Path(entry_path)
        if not cred_path.exists():
            return (entry_path, None)

        try:
            cred_data = json.loads(cred_path.read_text(encoding="utf-8"))
        except Exception:
            return (entry_path, None)

        access_token = cred_data.get("accessToken", "")
        if not access_token:
            return (entry_path, None)

        params = {"origin": "AI_EDITOR", "resourceType": "AGENTIC_REQUEST"}
        if PROFILE_ARN:
            params["profileArn"] = PROFILE_ARN

        try:
            resp = await client.get(
                KIRO_USAGE_ENDPOINT,
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "User-Agent": KIRO_USER_AGENT,
                    "x-amz-user-agent": KIRO_X_AMZ_USER_AGENT,
                },
            )
            if resp.status_code != 200:
                return (entry_path, None)
            payload = resp.json()
            return (entry_path, _parse_quota_for_endpoint(payload))
        except Exception:
            return (entry_path, None)

    # Fetch all in parallel
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        tasks = [fetch_single(client, entry) for entry in accounts]
        results = await asyncio.gather(*tasks)

    # Build cache and persist
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    quota_cache = {}

    for entry_path, result in results:
        if result and entry_path:
            result["lastFetched"] = now_iso
            quota_cache[entry_path] = result

    # Save to state.json
    if quota_cache:
        state_path = Path(ACCOUNTS_STATE_FILE)
        state_data = {}
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state_data = {}

        if "quota_cache" not in state_data:
            state_data["quota_cache"] = {}
        state_data["quota_cache"].update(quota_cache)

        try:
            tmp_path = state_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False)
            tmp_path.replace(state_path)
            logger.info(f"Quota cache updated: {len(quota_cache)} accounts")
        except Exception as e:
            logger.error(f"Failed to save quota cache: {e}")

    return quota_cache


def _parse_quota_for_endpoint(payload: dict) -> dict:
    """Parse Kiro usage API response into quota dict.

    Args:
        payload: Raw response from getUsageLimits API

    Returns:
        Dict with totalCredits, usedCredits, remainingCredits, packageName
    """
    usage_list = payload.get("usageBreakdownList") or []
    if not usage_list:
        return {"totalCredits": 0, "usedCredits": 0,
                "remainingCredits": 0, "packageName": "Free"}

    usage = usage_list[0] or {}
    total = float(usage.get("usageLimit") or usage.get("usageLimitWithPrecision") or 0)
    used = float(usage.get("currentUsage") or usage.get("currentUsageWithPrecision") or 0)

    ft = usage.get("freeTrialInfo") or {}
    if str(ft.get("freeTrialStatus") or "").upper() == "ACTIVE":
        total += float(ft.get("usageLimit") or ft.get("usageLimitWithPrecision") or 0)
        used += float(ft.get("currentUsage") or ft.get("currentUsageWithPrecision") or 0)

    for bonus in usage.get("bonuses") or []:
        total += float((bonus or {}).get("usageLimit") or 0)
        used += float((bonus or {}).get("currentUsage") or 0)

    remaining = max(total - used, 0)
    sub_title = str(
        payload.get("subscriptionInfo", {}).get("subscriptionTitle")
        or payload.get("subscriptionTitle")
        or payload.get("subscriptionType")
        or "Free"
    ).strip()

    return {
        "totalCredits": total,
        "usedCredits": used,
        "remainingCredits": remaining,
        "packageName": sub_title,
    }


@router.get("/v1/models", response_model=ModelList, dependencies=[Depends(verify_api_key)])
async def get_models(request: Request):
    """
    Return list of available models.
    
    Models are loaded at startup (blocking) and cached.
    This endpoint returns the cached list.
    
    Args:
        request: FastAPI Request for accessing app.state
    
    Returns:
        ModelList with available models in consistent format (with dots)
    """
    logger.info("Request to /v1/models")
    
    # Get available models based on mode
    if request.app.state.account_system:
        # Account system: collect models from all initialized accounts
        available_model_ids = request.app.state.account_manager.get_all_available_models()
    else:
        # Legacy: use resolver from first account
        account = request.app.state.account_manager.get_first_account()
        available_model_ids = account.model_resolver.get_available_models()
    
    # Build OpenAI-compatible model list
    openai_models = [
        OpenAIModel(
            id=model_id,
            owned_by="anthropic",
            description="Claude model via Kiro API"
        )
        for model_id in available_model_ids
    ]
    
    return ModelList(data=openai_models)


@router.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request, request_data: ChatCompletionRequest):
    """
    Chat completions endpoint - compatible with OpenAI API.
    
    Accepts requests in OpenAI format and translates them to Kiro API.
    Supports streaming and non-streaming modes.
    
    Args:
        request: FastAPI Request for accessing app.state
        request_data: Request in OpenAI ChatCompletionRequest format
    
    Returns:
        StreamingResponse for streaming mode
        JSONResponse for non-streaming mode
    
    Raises:
        HTTPException: On validation or API errors
    """
    logger.info(f"Request to /v1/chat/completions (model={request_data.model}, stream={request_data.stream})")
    
    # Note: prepare_new_request() and log_request_body() are now called by DebugLoggerMiddleware
    # This ensures debug logging works even for requests that fail Pydantic validation (422 errors)
    
    # Check for truncation recovery opportunities
    from kiro.truncation_state import get_tool_truncation, get_content_truncation
    from kiro.truncation_recovery import generate_truncation_tool_result, generate_truncation_user_message
    from kiro.models_openai import ChatMessage
    
    modified_messages = []
    tool_results_modified = 0
    content_notices_added = 0
    
    for msg in request_data.messages:
        # Check if this is a tool_result for a truncated tool call
        if msg.role == "tool" and msg.tool_call_id:
            truncation_info = get_tool_truncation(msg.tool_call_id)
            if truncation_info:
                # Modify tool_result content to include truncation notice
                synthetic = generate_truncation_tool_result(
                    tool_name=truncation_info.tool_name,
                    tool_use_id=msg.tool_call_id,
                    truncation_info=truncation_info.truncation_info
                )
                # Prepend truncation notice to original content
                modified_content = f"{synthetic['content']}\n\n---\n\nOriginal tool result:\n{msg.content}"
                
                # Create NEW ChatMessage object (Pydantic immutability)
                modified_msg = msg.model_copy(update={"content": modified_content})
                modified_messages.append(modified_msg)
                tool_results_modified += 1
                logger.debug(f"Modified tool_result for {msg.tool_call_id} to include truncation notice")
                continue  # Skip normal append since we already added modified version
        
        # Check if this is an assistant message with truncated content
        if msg.role == "assistant" and msg.content and isinstance(msg.content, str):
            truncation_info = get_content_truncation(msg.content)
            if truncation_info:
                # Add this message first
                modified_messages.append(msg)
                # Then add synthetic user message about truncation
                synthetic_user_msg = ChatMessage(
                    role="user",
                    content=generate_truncation_user_message()
                )
                modified_messages.append(synthetic_user_msg)
                content_notices_added += 1
                logger.debug(f"Added truncation notice after assistant message (hash: {truncation_info.message_hash})")
                continue  # Skip normal append since we already added it
        
        modified_messages.append(msg)
    
    if tool_results_modified > 0 or content_notices_added > 0:
        request_data.messages = modified_messages
        logger.info(f"Truncation recovery: modified {tool_results_modified} tool_result(s), added {content_notices_added} content notice(s)")
    
    # ==============================================================================
    # WebSearch Support - Path B: Auto-Injection (MCP Tool Emulation)
    # ==============================================================================
    
    # Auto-inject web_search tool if enabled (Path B - MCP emulation)
    if WEB_SEARCH_ENABLED:
        if request_data.tools is None:
            request_data.tools = []
        
        # Check if web_search already exists
        has_ws = any(
            getattr(tool, "type", None) == "function" and
            getattr(getattr(tool, "function", None), "name", None) == "web_search"
            for tool in request_data.tools
        )
        
        if not has_ws:
            from kiro.models_openai import Tool, ToolFunction
            web_search_tool = Tool(
                type="function",
                function=ToolFunction(
                    name="web_search",
                    description="Search the web for current information. Use when you need up-to-date data from the internet.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query"
                            }
                        },
                        "required": ["query"]
                    }
                )
            )
            request_data.tools.append(web_search_tool)
            logger.debug("Auto-injected web_search tool for MCP emulation (Path B)")
    
    # ==============================================================================
    # Account System: Account System Failover or Legacy Mode
    # ==============================================================================
    
    # Extract client_id for queue scheduling
    client_id = request.client.host if request.client else "unknown"
    
    # Get queue orchestrator (None if disabled)
    queue_orchestrator = getattr(request.app.state, 'queue_orchestrator', None)
    
    if request.app.state.account_system:
      try:
        # ==============================================================================
        # ACCOUNT SYSTEM ENABLED: Failover Loop
        # ==============================================================================
        from kiro.account_errors import classify_error, ErrorType
        
        account_manager = request.app.state.account_manager
        all_accounts = list(account_manager._accounts.keys())
        MAX_ATTEMPTS = len(all_accounts) * 2  # Full circle with margin
        
        last_error_message = None
        last_error_status = None
        tried_accounts = set()  # Track tried accounts in current failover loop
        
        for attempt in range(MAX_ATTEMPTS):
            # Get next available account (excluding already tried)
            account = await account_manager.get_next_account(
                request_data.model,
                exclude_accounts=tried_accounts
            )
            
            if account is None:
                # All accounts unavailable
                if len(all_accounts) == 1:
                    # Single account - return original error with original status code
                    raise HTTPException(
                        status_code=last_error_status or 503,
                        detail=last_error_message or "Account unavailable"
                    )
                else:
                    # Multiple accounts - generic error with context
                    detail = "No available accounts for this model."
                    if last_error_message:
                        detail += f" Last error: {last_error_message}"
                    raise HTTPException(status_code=503, detail=detail)
            
            # Mark account as tried in current failover loop
            tried_accounts.add(account.id)
            
            # Use objects from account
            auth_manager = account.auth_manager
            model_cache = account.model_cache
            model_resolver = account.model_resolver
            
            # Generate conversation ID
            conversation_id = generate_conversation_id()
            
            # Build payload for Kiro
            profile_arn_for_payload = ""
            if auth_manager.auth_type == AuthType.KIRO_DESKTOP and auth_manager.profile_arn:
                profile_arn_for_payload = auth_manager.profile_arn
            
            try:
                kiro_payload = build_kiro_payload(
                    request_data,
                    conversation_id,
                    profile_arn_for_payload
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            
            # Log Kiro payload (only serialize when debug logging is active)
            if debug_logger:
                try:
                    kiro_request_body = json.dumps(kiro_payload, ensure_ascii=False, indent=2).encode('utf-8')
                    debug_logger.log_kiro_request_body(kiro_request_body)
                except Exception as e:
                    logger.warning(f"Failed to log Kiro request: {e}")
            
            # Create HTTP client
            url = f"{auth_manager.api_host}/generateAssistantResponse"
            logger.debug(f"Kiro API URL: {url} (account: {account.id})")
            
            if request_data.stream:
                streaming_client = request.app.state.streaming_http_client
                http_client = KiroHttpClient(auth_manager, shared_client=streaming_client)
            else:
                shared_client = request.app.state.http_client
                http_client = KiroHttpClient(auth_manager, shared_client=shared_client)
            
            _slot_acquired = False
            try:
                # Acquire concurrency slot if queue is enabled
                if queue_orchestrator and QUEUE_ENABLED:
                    acquired = await queue_orchestrator._limiter.acquire(
                        account.id, timeout=queue_orchestrator._queue_timeout
                    )
                    if not acquired:
                        raise QueueTimeoutError(
                            wait_time=queue_orchestrator._queue_timeout,
                            request_id=client_id
                        )
                    _slot_acquired = True
                
                # Make request to Kiro API
                response = await http_client.request_with_retry(
                    "POST",
                    url,
                    kiro_payload,
                    stream=True
                )
                
                if response.status_code == 200:
                    # SUCCESS - report and return
                    await account_manager.report_success(account.id, request_data.model)
                    
                    # Pass raw Pydantic objects for lazy token counting
                    # model_dump() is deferred to only when context_usage fallback is needed
                    request_messages_raw = request_data.messages
                    request_tools_raw = request_data.tools
                    
                    if request_data.stream:
                        # Streaming mode
                        usage_collector: dict = {}
                        
                        async def stream_wrapper():
                            streaming_error = None
                            client_disconnected = False
                            try:
                                async def make_retry_request():
                                    return await http_client.request_with_retry(
                                        "POST", url, kiro_payload, stream=True
                                    )
                                
                                async for chunk in stream_with_first_token_retry(
                                    make_request=make_retry_request,
                                    client=http_client.client,
                                    model=request_data.model,
                                    model_cache=model_cache,
                                    auth_manager=auth_manager,
                                    initial_response=response,
                                    request_messages=request_messages_raw,
                                    request_tools=request_tools_raw
                                ):
                                    # Capture credits from final chunk
                                    if '"credits_used"' in chunk:
                                        try:
                                            data_str = chunk[len("data: "):].strip()
                                            chunk_json = json.loads(data_str)
                                            cu = chunk_json.get("usage", {}).get("credits_used")
                                            if cu:
                                                usage_collector["credits_used"] = cu
                                        except (json.JSONDecodeError, ValueError, TypeError):
                                            pass
                                    yield chunk
                            except GeneratorExit:
                                client_disconnected = True
                                logger.debug("Client disconnected during streaming (GeneratorExit in routes)")
                            except Exception as e:
                                streaming_error = e
                                try:
                                    yield "data: [DONE]\n\n"
                                except Exception:
                                    pass
                                raise
                            finally:
                                await http_client.close()
                                # Report credit usage from streaming
                                credits = usage_collector.get("credits_used")
                                if credits:
                                    await account_manager.report_usage(account.id, credits)
                                if streaming_error:
                                    error_type = type(streaming_error).__name__
                                    error_msg = str(streaming_error) if str(streaming_error) else "(empty message)"
                                    logger.error(f"HTTP 500 - POST /v1/chat/completions (streaming) - [{error_type}] {error_msg[:100]}")
                                elif client_disconnected:
                                    logger.info(f"HTTP 200 - POST /v1/chat/completions (streaming) - client disconnected")
                                else:
                                    logger.info(f"HTTP 200 - POST /v1/chat/completions (streaming) - completed")
                                if debug_logger:
                                    if streaming_error:
                                        debug_logger.flush_on_error(500, str(streaming_error))
                                    else:
                                        debug_logger.discard_buffers()
                        
                        return StreamingResponse(stream_wrapper(), media_type="text/event-stream")
                    
                    else:
                        # Non-streaming mode
                        openai_response = await collect_stream_response(
                            http_client.client,
                            response,
                            request_data.model,
                            model_cache,
                            auth_manager,
                            request_messages=request_messages_raw,
                            request_tools=request_tools_raw
                        )
                        
                        await http_client.close()
                        logger.info(f"HTTP 200 - POST /v1/chat/completions (non-streaming) - completed")
                        
                        # Report credit usage
                        credits = openai_response.get("usage", {}).get("credits_used")
                        if credits:
                            await account_manager.report_usage(account.id, credits)
                        
                        if debug_logger:
                            debug_logger.discard_buffers()
                        
                        return JSONResponse(content=openai_response)
                
                else:
                    # ERROR - classify and decide
                    try:
                        error_content = await response.aread()
                    except Exception:
                        error_content = b"Unknown error"
                    
                    await http_client.close()
                    error_text = error_content.decode('utf-8', errors='replace')
                    
                    # Extract error reason and save for final return
                    error_reason = None
                    try:
                        error_json = json.loads(error_text)
                        from kiro.kiro_errors import enhance_kiro_error
                        error_info = enhance_kiro_error(error_json)
                        error_reason = error_info.reason
                        last_error_message = error_info.user_message
                        last_error_status = response.status_code
                        logger.debug(f"Original Kiro error: {error_info.original_message} (reason: {error_info.reason})")
                    except (json.JSONDecodeError, KeyError):
                        last_error_message = error_text
                        last_error_status = response.status_code
                    
                    # Classify error
                    error_type = classify_error(response.status_code, error_reason)
                    
                    if error_type == ErrorType.FATAL:
                        # FATAL - return to client immediately
                        await account_manager.report_failure(
                            account.id, request_data.model, error_type,
                            response.status_code, error_reason
                        )
                        
                        logger.warning(f"HTTP {response.status_code} - POST /v1/chat/completions - {last_error_message[:100]}")
                        
                        if debug_logger:
                            debug_logger.flush_on_error(response.status_code, last_error_message)
                        
                        return JSONResponse(
                            status_code=response.status_code,
                            content={
                                "error": {
                                    "message": last_error_message,
                                    "type": "kiro_api_error",
                                    "code": response.status_code
                                }
                            }
                        )
                    
                    else:  # ErrorType.RECOVERABLE
                        # RECOVERABLE - try next account
                        await account_manager.report_failure(
                            account.id, request_data.model, error_type,
                            response.status_code, error_reason
                        )
                        
                        # Single account - no point in failover, break immediately
                        if len(all_accounts) == 1:
                            break
                        
                        continue  # Next iteration
            
            except HTTPException as e:
                await http_client.close()
                
                # Network errors (502/504 from request_with_retry) = RECOVERABLE
                # These are thrown ONLY for network-level issues (timeouts, connection errors)
                # NOT for HTTP-level errors (which are returned as response objects)
                if e.status_code in (502, 504):
                    # Network error → try next account
                    await account_manager.report_failure(
                        account.id, request_data.model, ErrorType.RECOVERABLE,
                        e.status_code, None
                    )
                    
                    last_error_message = str(e.detail)
                    last_error_status = e.status_code
                    
                    # Single account - no point in failover, break immediately
                    if len(all_accounts) == 1:
                        break
                    
                    logger.warning(f"Network error on account {account.id}, trying next account")
                    continue  # Try next account
                
                # All other HTTPException (400, 500, etc.) = application errors
                # These come from build_kiro_payload() or other places → re-raise immediately
                logger.error(f"HTTP {e.status_code} - POST /v1/chat/completions - {e.detail}")
                if debug_logger:
                    debug_logger.flush_on_error(e.status_code, str(e.detail))
                raise
            except AccountRateLimited as e:
                await http_client.close()
                # Report rate limit and try next account
                await account_manager.report_rate_limit(e.account_id, e.retry_after)
                tried_accounts.add(e.account_id)
                last_error_message = f"Account {e.account_id} rate limited"
                last_error_status = 429
                logger.warning(f"Account {e.account_id} rate limited (retry_after={e.retry_after}s), trying next account")
                
                # Single account - no point in failover
                if len(all_accounts) == 1:
                    break
                
                continue  # Try next account
            except (QueueTimeoutError, QueueFullError):
                # Let queue errors propagate to the outer handler
                raise
            except Exception as e:
                await http_client.close()
                logger.error(f"Internal error: {e}", exc_info=True)
                logger.error(f"HTTP 500 - POST /v1/chat/completions - {str(e)[:100]}")
                if debug_logger:
                    debug_logger.flush_on_error(500, str(e))
                raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
            finally:
                # Release concurrency slot if acquired
                if _slot_acquired:
                    queue_orchestrator._limiter.release(account.id)
        
        # All attempts exhausted
        if len(all_accounts) == 1:
            # Single account - return its original error
            # last_error_status and last_error_message are guaranteed to be set
            raise HTTPException(
                status_code=last_error_status,
                detail=last_error_message
            )
        else:
            # Multiple accounts - generic error with context
            detail = "All accounts failed after full circle."
            if last_error_message:
                detail += f" Last error: {last_error_message}"
            raise HTTPException(status_code=503, detail=detail)
      
      except QueueTimeoutError as e:
        logger.warning(f"Queue timeout for client {client_id}: {e}")
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "Server busy, please retry later", "type": "queue_timeout", "code": 503}}
        )
      except QueueFullError as e:
        logger.warning(f"Queue full for client {client_id}: {e}")
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "Too many pending requests", "type": "queue_full", "code": 503}}
        )
    
    else:
        # ==============================================================================
        # LEGACY MODE: Single Account (no failover)
        # ==============================================================================
        account = request.app.state.account_manager.get_first_account()
        if not account.auth_manager:
            logger.error("No initialized accounts available (legacy mode)")
            raise HTTPException(503, "No initialized accounts available")
        auth_manager = account.auth_manager
        model_cache = account.model_cache
        model_resolver = account.model_resolver
    
    # Generate conversation ID for Kiro API (random UUID, not used for tracking)
    conversation_id = generate_conversation_id()
    
    # Build payload for Kiro
    # profileArn is only needed for Kiro Desktop auth
    # AWS SSO OIDC (Builder ID) users don't need profileArn and it causes 403 if sent
    profile_arn_for_payload = ""
    if auth_manager.auth_type == AuthType.KIRO_DESKTOP and auth_manager.profile_arn:
        profile_arn_for_payload = auth_manager.profile_arn
    
    try:
        kiro_payload = build_kiro_payload(
            request_data,
            conversation_id,
            profile_arn_for_payload
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Log Kiro payload
    try:
        kiro_request_body = json.dumps(kiro_payload, ensure_ascii=False, indent=2).encode('utf-8')
        if debug_logger:
            debug_logger.log_kiro_request_body(kiro_request_body)
    except Exception as e:
        logger.warning(f"Failed to log Kiro request: {e}")
    
    # Create HTTP client with retry logic
    # For streaming: use per-request client to avoid CLOSE_WAIT leak on VPN disconnect (issue #54)
    # For non-streaming: use shared client for connection pooling
    url = f"{auth_manager.api_host}/generateAssistantResponse"
    logger.debug(f"Kiro API URL: {url}")
    
    if request_data.stream:
        # Streaming mode: per-request client prevents orphaned connections
        # when network interface changes (VPN disconnect/reconnect)
        http_client = KiroHttpClient(auth_manager, shared_client=None)
    else:
        # Non-streaming mode: shared client for efficient connection reuse
        shared_client = request.app.state.http_client
        http_client = KiroHttpClient(auth_manager, shared_client=shared_client)
    try:
        # Make request to Kiro API (for both streaming and non-streaming modes)
        # Important: we wait for Kiro response BEFORE returning StreamingResponse,
        # so that 200 OK means Kiro accepted the request and started responding
        response = await http_client.request_with_retry(
            "POST",
            url,
            kiro_payload,
            stream=True
        )
        
        if response.status_code != 200:
            try:
                error_content = await response.aread()
            except Exception:
                error_content = b"Unknown error"
            
            await http_client.close()
            error_text = error_content.decode('utf-8', errors='replace')
            
            # Try to parse JSON response from Kiro to extract error message
            error_message = error_text
            try:
                error_json = json.loads(error_text)
                # Enhance Kiro API errors with user-friendly messages
                from kiro.kiro_errors import enhance_kiro_error
                error_info = enhance_kiro_error(error_json)
                error_message = error_info.user_message
                # Log original error for debugging
                logger.debug(f"Original Kiro error: {error_info.original_message} (reason: {error_info.reason})")
            except (json.JSONDecodeError, KeyError):
                pass
            
            # Log access log for error (before flush, so it gets into app_logs)
            logger.warning(
                f"HTTP {response.status_code} - POST /v1/chat/completions - {error_message[:100]}"
            )
            
            # Flush debug logs on error ("errors" mode)
            if debug_logger:
                debug_logger.flush_on_error(response.status_code, error_message)
            
            # Return error in OpenAI API format
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "error": {
                        "message": error_message,
                        "type": "kiro_api_error",
                        "code": response.status_code
                    }
                }
            )
        
        # Prepare data for fallback token counting
        # Convert Pydantic models to dicts for tokenizer
        messages_for_tokenizer = [msg.model_dump() for msg in request_data.messages]
        tools_for_tokenizer = [tool.model_dump() for tool in request_data.tools] if request_data.tools else None
        
        if request_data.stream:
            # Streaming mode with first token retry
            async def stream_wrapper():
                streaming_error = None
                client_disconnected = False
                try:
                    # Create retry request function for retries
                    async def make_retry_request():
                        return await http_client.request_with_retry(
                            "POST", url, kiro_payload, stream=True
                        )
                    
                    # Use retry wrapper with initial response
                    async for chunk in stream_with_first_token_retry(
                        make_request=make_retry_request,
                        client=http_client.client,
                        model=request_data.model,
                        model_cache=model_cache,
                        auth_manager=auth_manager,
                        initial_response=response,
                        request_messages=messages_for_tokenizer,
                        request_tools=tools_for_tokenizer
                    ):
                        yield chunk
                except GeneratorExit:
                    # Client disconnected - this is normal
                    client_disconnected = True
                    logger.debug("Client disconnected during streaming (GeneratorExit in routes)")
                except Exception as e:
                    streaming_error = e
                    # Try to send [DONE] to client before finishing
                    # so client doesn't "hang" waiting for data
                    try:
                        yield "data: [DONE]\n\n"
                    except Exception:
                        pass  # Client already disconnected
                    raise
                finally:
                    await http_client.close()
                    # Log access log for streaming (success or error)
                    if streaming_error:
                        error_type = type(streaming_error).__name__
                        error_msg = str(streaming_error) if str(streaming_error) else "(empty message)"
                        logger.error(f"HTTP 500 - POST /v1/chat/completions (streaming) - [{error_type}] {error_msg[:100]}")
                    elif client_disconnected:
                        logger.info(f"HTTP 200 - POST /v1/chat/completions (streaming) - client disconnected")
                    else:
                        logger.info(f"HTTP 200 - POST /v1/chat/completions (streaming) - completed")
                    # Write debug logs AFTER streaming completes
                    if debug_logger:
                        if streaming_error:
                            debug_logger.flush_on_error(500, str(streaming_error))
                        else:
                            debug_logger.discard_buffers()
            
            return StreamingResponse(stream_wrapper(), media_type="text/event-stream")
        
        else:
            
            # Non-streaming mode - collect entire response
            openai_response = await collect_stream_response(
                http_client.client,
                response,
                request_data.model,
                model_cache,
                auth_manager,
                request_messages=messages_for_tokenizer,
                request_tools=tools_for_tokenizer
            )
            
            await http_client.close()
            
            # Log access log for non-streaming success
            logger.info(f"HTTP 200 - POST /v1/chat/completions (non-streaming) - completed")
            
            # Write debug logs after non-streaming request completes
            if debug_logger:
                debug_logger.discard_buffers()
            
            return JSONResponse(content=openai_response)
    
    except HTTPException as e:
        await http_client.close()
        
        # Network errors (502/504 from request_with_retry) = RECOVERABLE
        # In legacy mode, we still log them but re-raise (no failover available)
        if e.status_code in (502, 504):
            logger.warning(f"Network error (legacy mode, no failover available)")
        
        # Log access log for HTTP error
        logger.error(f"HTTP {e.status_code} - POST /v1/chat/completions - {e.detail}")
        # Flush debug logs on HTTP error ("errors" mode)
        if debug_logger:
            debug_logger.flush_on_error(e.status_code, str(e.detail))
        raise
    except Exception as e:
        await http_client.close()
        logger.error(f"Internal error: {e}", exc_info=True)
        # Log access log for internal error
        logger.error(f"HTTP 500 - POST /v1/chat/completions - {str(e)[:100]}")
        # Flush debug logs on internal error ("errors" mode)
        if debug_logger:
            debug_logger.flush_on_error(500, str(e))
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
