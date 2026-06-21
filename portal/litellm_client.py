from __future__ import annotations

from typing import Any

import requests

from .settings import settings


class LiteLLMError(RuntimeError):
    pass


def _headers(auth_key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {auth_key or settings.litellm_master_key}",
        "Content-Type": "application/json",
    }


def _post(path: str, payload: dict) -> dict:
    url = f"{settings.litellm_base_url}{path}"
    try:
        response = requests.post(url, headers=_headers(), json=payload, timeout=15)
    except requests.RequestException as exc:
        raise LiteLLMError(f"LiteLLM request failed: {exc}") from exc
    if response.status_code >= 400:
        raise LiteLLMError(f"LiteLLM {path} failed: {response.status_code} {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise LiteLLMError(f"LiteLLM {path} returned non-json response") from exc


def _post_with_auth(path: str, payload: dict, auth_key: str) -> dict:
    url = f"{settings.litellm_base_url}{path}"
    try:
        response = requests.post(url, headers=_headers(auth_key), json=payload, timeout=120)
    except requests.RequestException as exc:
        raise LiteLLMError(f"LiteLLM request failed: {exc}") from exc
    if response.status_code >= 400:
        raise LiteLLMError(f"LiteLLM {path} failed: {response.status_code} {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise LiteLLMError(f"LiteLLM {path} returned non-json response") from exc


def _get(path: str, params: dict | None = None, auth_key: str | None = None) -> dict:
    url = f"{settings.litellm_base_url}{path}"
    try:
        response = requests.get(url, headers=_headers(auth_key), params=params or {}, timeout=20)
    except requests.RequestException as exc:
        raise LiteLLMError(f"LiteLLM request failed: {exc}") from exc
    if response.status_code >= 400:
        raise LiteLLMError(f"LiteLLM {path} failed: {response.status_code} {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise LiteLLMError(f"LiteLLM {path} returned non-json response") from exc


def ensure_team_member(email: str, team_id: str | None) -> None:
    if not team_id:
        return
    try:
        _post(
            "/team/member_add",
            {
                "team_id": team_id,
                "member": {
                    "role": "user",
                    "user_id": email,
                },
            },
        )
    except LiteLLMError as exc:
        message = str(exc).lower()
        if "already" not in message and "exist" not in message and "duplicate" not in message:
            raise


def generate_pending_key(email: str, team_id: str | None = None) -> str:
    payload = {
        "models": ["__pending_payment__"],
        "duration": "365d",
        "max_budget": 0.0,
        "rpm_limit": 1,
        "tpm_limit": 1,
        "user_id": email,
        "team_id": team_id,
        "metadata": {
            "source": "ai-proxy-portal",
            "email": email,
            "status": "pending_payment",
            "team_id": team_id or "",
        },
    }
    data = _post("/key/generate", payload)
    key = data.get("key")
    if not key:
        raise LiteLLMError("LiteLLM did not return generated key")
    try:
        _post("/key/block", {"key": key})
    except LiteLLMError:
        # max_budget=0 and invalid model list still prevent practical usage; do not fail registration.
        pass
    return key


def activate_key(api_key: str, email: str, plan: Any, out_trade_no: str, max_budget_usd: float | None = None) -> None:
    request_limits = {
        "total_request_limit": getattr(plan, "total_request_limit", None),
        "five_hour_request_limit": getattr(plan, "five_hour_request_limit", None),
        "daily_request_limit": getattr(plan, "daily_request_limit", None),
        "weekly_request_limit": getattr(plan, "weekly_request_limit", None),
        "monthly_request_limit": getattr(plan, "monthly_request_limit", None),
        "request_tier": getattr(plan, "request_tier", None),
    }
    rate_limits = {
        "rpm_limit": plan.rpm_limit,
        "tpm_limit": plan.tpm_limit,
        "rpm_tier": getattr(plan, "rpm_tier", None),
        "tpm_tier": getattr(plan, "tpm_tier", None),
    }
    target_budget = float(max_budget_usd if max_budget_usd is not None else plan.max_budget_usd)
    team_id = getattr(plan, "team_id", None)
    if team_id:
        # LiteLLM validates the current key model list when attaching a team.
        # Clear stale models first so old catalog entries do not block a paid activation.
        _post(
            "/key/update",
            {
                "key": api_key,
                "models": plan.models,
                "user_id": email,
            },
        )
        ensure_team_member(email, team_id)
    update_payload = {
        "key": api_key,
        "models": plan.models,
        "max_budget": target_budget,
        "duration": plan.duration,
        "rpm_limit": plan.rpm_limit,
        "tpm_limit": plan.tpm_limit,
        "team_id": team_id,
        "user_id": email,
        "metadata": {
            "source": "ai-proxy-portal",
            "email": email,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "amount_cny": str(plan.price_cny),
            "order_budget_usd": plan.max_budget_usd,
            "max_budget_usd": target_budget,
            "out_trade_no": out_trade_no,
            "status": "paid",
            "order_type": getattr(plan, "order_type", "plan"),
            "team_id": team_id or "",
            "request_limits": request_limits,
            "rate_limits": rate_limits,
        },
    }
    _post("/key/update", update_payload)
    _post("/key/unblock", {"key": api_key})


def block_key(api_key: str) -> None:
    _post("/key/block", {"key": api_key})


def unblock_key(api_key: str) -> None:
    _post("/key/unblock", {"key": api_key})


def update_key_budget(api_key: str, max_budget_usd: float, metadata: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "key": api_key,
        "max_budget": max(float(max_budget_usd), 0.0),
    }
    if metadata is not None:
        payload["metadata"] = metadata
    _post("/key/update", payload)


def update_key_spend(api_key: str, spend_usd: float, metadata: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "key": api_key,
        "spend": max(float(spend_usd), 0.0),
    }
    if metadata is not None:
        payload["metadata"] = metadata
    _post("/key/update", payload)


def _add_chat_params(
    payload: dict[str, Any],
    *,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
) -> None:
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if top_p is not None and top_p != 1:
        payload["top_p"] = top_p
    if presence_penalty is not None and presence_penalty != 0:
        payload["presence_penalty"] = presence_penalty
    if frequency_penalty is not None and frequency_penalty != 0:
        payload["frequency_penalty"] = frequency_penalty


def chat_completion(
    api_key: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    _add_chat_params(
        payload,
        max_tokens=max_tokens,
        top_p=top_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
    )
    if user_id:
        payload["user"] = user_id
    if metadata:
        payload["metadata"] = metadata
    return _post_with_auth("/v1/chat/completions", payload, api_key)


def chat_completion_stream(
    api_key: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> requests.Response:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    _add_chat_params(
        payload,
        max_tokens=max_tokens,
        top_p=top_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
    )
    if user_id:
        payload["user"] = user_id
    if metadata:
        payload["metadata"] = metadata
    url = f"{settings.litellm_base_url}/v1/chat/completions"
    try:
        response = requests.post(url, headers=_headers(api_key), json=payload, stream=True, timeout=(15, 180))
    except requests.RequestException as exc:
        raise LiteLLMError(f"LiteLLM request failed: {exc}") from exc
    if response.status_code >= 400:
        text = response.text[:500]
        response.close()
        raise LiteLLMError(f"LiteLLM /v1/chat/completions failed: {response.status_code} {text}")
    return response


def chat_completion_as_service(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    _add_chat_params(
        payload,
        max_tokens=max_tokens,
        top_p=top_p,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
    )
    if user_id:
        payload["user"] = user_id
    if metadata:
        payload["metadata"] = metadata
    return _post_with_auth("/v1/chat/completions", payload, settings.litellm_master_key)


def get_key_info(api_key: str, auth_key: str | None = None) -> dict:
    data = _get("/key/info", {"key": api_key}, auth_key=auth_key)
    return data.get("info") or {}


def get_model_info() -> list[dict]:
    data = _get("/model/info")
    return data.get("data") or []


def get_teams() -> list[dict]:
    data = _get("/team/list")
    if isinstance(data, list):
        return data
    return data.get("teams") or data.get("data") or []


def get_spend_logs(
    user_id: str | None,
    start_date: str,
    end_date: str,
    max_pages: int = 10,
    *,
    api_key: str | None = None,
    key_alias: str | None = None,
) -> list[dict]:
    if not any((user_id, api_key, key_alias)):
        raise LiteLLMError("LiteLLM spend logs query requires user_id, api_key, or key_alias")
    logs: list[dict] = []
    for page in range(1, max_pages + 1):
        params: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "page_size": 100,
        }
        if user_id:
            params["user_id"] = user_id
        if api_key:
            params["api_key"] = api_key
        if key_alias:
            params["key_alias"] = key_alias
        data = _get(
            "/spend/logs/v2",
            params,
        )
        page_logs = data.get("data") or []
        logs.extend(page_logs)
        total_pages = int(data.get("total_pages") or 0)
        if not page_logs or page >= total_pages:
            break
    return logs
