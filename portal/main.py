from __future__ import annotations

import base64
import binascii
import hashlib
import json
import random
import re
import requests
import string
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .auth import hash_password, new_token, verify_password
from .catalog import (
    CatalogError,
    activation_from_snapshot,
    custom_spec,
    get_plan,
    load_catalog,
    model_map as catalog_model_map,
    public_catalog,
    save_catalog,
    team_config,
)
from .litellm_client import (
    LiteLLMError,
    activate_key,
    block_key,
    chat_completion,
    chat_completion_stream,
    generate_pending_key,
    get_key_info,
    get_model_info,
    get_spend_logs,
    get_teams,
    update_key_budget,
    update_key_spend,
)
from .mailer import send_password_reset_email, send_payment_success_email, send_registration_email
from .settings import settings
from .zpay import PAY_TYPES, build_payment_url, is_configured as zpay_is_configured, verify as verify_zpay


EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSET_DIR = Path(__file__).resolve().parents[1] / "pngs"
SESSION_COOKIE = "portal_session"
COOKIE_DAYS = 14
SESSION_IDLE_SECONDS = 60 * 60
RESET_MINUTES = 30
LOCAL_TZ = timezone(timedelta(hours=8))
MODEL_INFO_CACHE_TTL_SECONDS = 600
API_KEY_USAGE_LOG_MAX_PAGES = 1
USAGE_CACHE_TTL_SECONDS = 20
_MODEL_INFO_CACHE: tuple[float, dict[str, dict]] = (0.0, {})
_USAGE_CACHE: dict[int, tuple[float, dict]] = {}
_USAGE_INFLIGHT: set[int] = set()
_USAGE_LOCK = threading.Lock()
CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CHAT_IMAGE_MAX_COUNT = 4
CHAT_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
CHAT_IMAGE_DATA_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$")

DOC_ITEMS = [
    {
        "id": "api",
        "date": "2026-06-17",
        "tag": "API",
        "title": "API 调用文档",
        "summary": "OpenAI 兼容接口、Base URL、Chat Completions、Python / JavaScript 示例和流式输出。",
        "url": "docs/api",
        "page_key": "docs:api",
    },
    {
        "id": "codex",
        "date": "2026-06-17",
        "tag": "客户端",
        "title": "Codex 安装配置",
        "summary": "把 Codex 配置为通过AI Proxy调用模型，并配置环境变量与 config.toml。",
        "url": "docs/codex",
        "page_key": "docs:codex",
    },
    {
        "id": "claude-code",
        "date": "2026-06-17",
        "tag": "客户端",
        "title": "Claude Code 安装配置",
        "summary": "Claude Code 的环境变量、settings.json 和常见 TPM 注意事项。",
        "url": "docs/claude-code",
        "page_key": "docs:claude-code",
    },
    {
        "id": "agent-client",
        "date": "2026-06-17",
        "tag": "Agent",
        "title": "Agent 监控客户端配置",
        "summary": "通过 Codex / Claude Code hooks 上报任务状态、工具步骤、token 和上下文窗口。",
        "url": "docs/agent-client",
        "page_key": "docs:agent-client",
    },
]

NEWS_ITEMS = [
    {
        "id": "open-source-package",
        "date": "2026-06-20",
        "tag": "开源版",
        "title": "AI Proxy Portal 开源版准备完成",
        "summary": "开源版保留门户、Key 管理、用量分析、套餐支付、聊天和文档页面，同时移除了生产数据与私有配置。",
        "body": [
            "这是一个安全的示例新闻条目，用来展示门户的新闻列表和详情页能力。",
            "真实部署时，可以把 NEWS_ITEMS 改成读取数据库、CMS 或你自己的公告配置。",
            "发布前请继续保持生产域名、密钥、支付账号、邮件账号、用户数据和内部运营内容不进入公开仓库。",
        ],
        "changes": [
            "保留新闻列表、详情页、浏览计数和 API 输出。",
            "移除内部模型发布说明和私有图片素材。",
            "默认配置全部改为 localhost 或 example.com 示例值。",
        ],
    },
]

app = FastAPI(title="AI Proxy Portal", version="0.2.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ApiKeyLoginRequest(BaseModel):
    api_key: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class CreateOrderRequest(BaseModel):
    plan_id: str = "starter"
    pay_type: str = "alipay"
    email: str | None = None
    custom: dict[str, Any] | None = None


class AdminCatalogRequest(BaseModel):
    catalog: dict[str, Any]


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None


class ChatThreadSaveRequest(BaseModel):
    title: str
    model: str
    messages: list[dict[str, Any]]
    last_usage: dict[str, Any] | None = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _clean_email(email: str) -> str:
    value = email.strip().lower()
    if not EMAIL_RE.match(value):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    return value


def _mask_email(email: str | None) -> str | None:
    if not email:
        return None
    value = str(email).strip()
    if "@" not in value:
        return value[:2] + "***" if len(value) > 2 else "***"
    local, domain = value.split("@", 1)
    if not local:
        masked_local = "***"
    elif len(local) == 1:
        masked_local = f"{local[0]}***"
    elif len(local) == 2:
        masked_local = f"{local[0]}***{local[-1]}"
    else:
        masked_local = f"{local[:2]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def _clean_password(password: str) -> str:
    value = password.strip()
    if len(value) < 8:
        raise HTTPException(status_code=422, detail="密码至少需要 8 位")
    if len(value) > 128:
        raise HTTPException(status_code=422, detail="密码过长")
    return value


def _clean_api_key(api_key: str) -> str:
    value = api_key.strip()
    if len(value) < 8:
        raise HTTPException(status_code=422, detail="API Key 格式不正确")
    return value


def _external_key_email(api_key: str, key_info: dict[str, Any]) -> str:
    metadata = key_info.get("metadata") or {}
    candidates = []
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("email"), metadata.get("user_email"), metadata.get("user_id")])
    candidates.extend([key_info.get("user_email"), key_info.get("user_id"), key_info.get("key_alias")])
    for candidate in candidates:
        if isinstance(candidate, str) and EMAIL_RE.match(candidate.strip()):
            email = candidate.strip().lower()
            existing = db.get_user_by_email(email)
            if not existing or existing.get("litellm_key") == api_key:
                return email
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    return f"key-{digest}@external.ai-proxy.local"


def _import_external_key_user(api_key: str, key_info: dict[str, Any]) -> dict:
    email = _external_key_email(api_key, key_info)
    existing = db.get_user_by_email(email)
    if existing:
        if existing.get("litellm_key") == api_key:
            return existing
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
        email = f"key-{digest}@external.ai-proxy.local"
    key_status = "pending" if key_info.get("blocked") else "active"
    user = db.create_user(email, api_key, key_status=key_status)
    db.log_event("auth", "external api key imported", email)
    return user


def _user_from_api_key(api_key: str, *, import_external: bool = False) -> dict | None:
    user = db.get_user_by_litellm_key(api_key)
    if user or not import_external:
        return user
    try:
        key_info = get_key_info(api_key)
    except LiteLLMError as exc:
        db.log_event("auth", f"external api key lookup failed: {_redact_error(exc)}")
        return None
    if not key_info:
        return None
    return _import_external_key_user(api_key, key_info)


def _new_order_no() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return time.strftime("%Y%m%d%H%M%S") + suffix


def _view_count_label(views: int) -> str:
    return f"浏览量 {views:,}"


def _view_count_meta(views: int) -> str:
    return f'      <div class="doc-view-meta"><span>{escape(_view_count_label(views))}</span></div>'


def _inject_doc_view_count(html: str, views: int) -> str:
    return html.replace("      <h1>", f"{_view_count_meta(views)}\n      <h1>", 1)


def _static_doc_page(filename: str, page_key: str, *, nested: bool = True) -> str:
    views = db.increment_page_view(page_key)
    html = (STATIC_DIR / filename).read_text(encoding="utf-8")
    if nested:
        html = html.replace('href="static/', 'href="../static/')
        html = html.replace('src="static/', 'src="../static/')
        html = html.replace('href="./#docs"', 'href="../docs/"')
        html = html.replace('href="../#docs"', 'href="../docs/"')
        html = html.replace('href="../#agents"', 'href="../docs/"')
        html = html.replace(">返回门户</a>", ">返回调用文档</a>")
        html = html.replace(">返回 Agent 看板</a>", ">返回调用文档</a>")
    return _inject_doc_view_count(html, views)


def _public_doc_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "date": item["date"],
        "tag": item["tag"],
        "title": item["title"],
        "summary": item["summary"],
        "url": item["url"],
        "page_key": item["page_key"],
        "views": db.get_page_view(item["page_key"]),
    }


def _sort_by_date_and_views(items: list[dict[str, Any]], sort_by: str = "updated") -> list[dict[str, Any]]:
    if sort_by == "views":
        return sorted(items, key=lambda item: (int(item.get("views") or 0), str(item.get("date") or "")), reverse=True)
    return sorted(items, key=lambda item: (str(item.get("date") or ""), int(item.get("views") or 0)), reverse=True)


def _card_html(item: dict[str, Any], *, href: str) -> str:
    views = item.get("views")
    view_meta = "" if views is None else f"<span>{escape(_view_count_label(int(views)))}</span>"
    return f"""        <a class="news-card" href="{escape(href)}">
          <div class="news-card-meta">
            <span>{escape(str(item.get("date") or ""))}</span>
            <span>{escape(str(item.get("tag") or ""))}</span>
            {view_meta}
          </div>
          <h3>{escape(str(item.get("title") or ""))}</h3>
          <p>{escape(str(item.get("summary") or ""))}</p>
          <strong>查看详情</strong>
        </a>"""


def _listing_page_html(*, title: str, subtitle: str, back_href: str, cards: str, sort_by: str) -> str:
    updated_active = " active" if sort_by != "views" else ""
    views_active = " active" if sort_by == "views" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} - AI Proxy AI 控制台</title>
    <link rel="stylesheet" href="../static/styles.css?v=20260617-portal-38" />
  </head>
  <body class="doc-page">
    <main class="doc listing-page">
      <a href="{escape(back_href)}" class="back-link">返回门户</a>
      <div class="listing-head">
        <div>
          <h1>{escape(title)}</h1>
          <p>{escape(subtitle)}</p>
        </div>
        <div class="listing-sort" aria-label="排序方式">
          <a class="{updated_active.strip()}" href="?sort=updated">按更新时间</a>
          <a class="{views_active.strip()}" href="?sort=views">按浏览量</a>
        </div>
      </div>
      <div class="news-list listing-grid">
{cards}
      </div>
    </main>
  </body>
</html>"""


def _docs_listing_html(sort_by: str = "updated") -> str:
    items = _sort_by_date_and_views([_public_doc_item(item) for item in DOC_ITEMS], sort_by)
    cards = "\n".join(_card_html(item, href=item["id"]) for item in items)
    return _listing_page_html(
        title="调用文档",
        subtitle="选择一篇文档查看配置步骤、调用示例和客户端接入说明。",
        back_href="../#docs",
        cards=cards,
        sort_by=sort_by,
    )


def _public_news_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "date": item["date"],
        "tag": item["tag"],
        "title": item["title"],
        "summary": item["summary"],
        "url": f"news/{item['id']}",
        "views": db.get_page_view(f"news:{item['id']}"),
    }


def _get_news_item(news_id: str) -> dict[str, Any]:
    for item in NEWS_ITEMS:
        if item["id"] == news_id:
            return item
    raise HTTPException(status_code=404, detail="新闻不存在")


def _news_listing_html(sort_by: str = "updated") -> str:
    items = _sort_by_date_and_views([_public_news_item(item) for item in NEWS_ITEMS], sort_by)
    cards = "\n".join(_card_html(item, href=item["id"]) for item in items)
    return _listing_page_html(
        title="新闻更新",
        subtitle="模型变更、套餐调整和调用说明会同步在这里。",
        back_href="../#news",
        cards=cards,
        sort_by=sort_by,
    )


def _news_detail_html(item: dict[str, Any], views: int) -> str:
    paragraphs = "\n".join(f"      <p>{escape(text)}</p>" for text in item.get("body", []))
    changes = "\n".join(f"        <li>{escape(text)}</li>" for text in item.get("changes", []))
    image_cards = "\n".join(
        f"""        <figure class="news-image-card">
          <img src="{escape(image["src"])}" alt="{escape(image["alt"])}" />
          <figcaption>{escape(image["caption"])}</figcaption>
        </figure>"""
        for image in item.get("images", [])
    )
    image_gallery = f"""
      <h2>能力图片</h2>
      <div class="news-image-grid">
{image_cards}
      </div>
""" if image_cards else ""
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(item["title"])} - AI Proxy AI 控制台</title>
    <link rel="stylesheet" href="../static/styles.css?v=20260617-portal-38" />
  </head>
  <body class="doc-page">
    <main class="doc news-detail">
      <a href="../news/" class="back-link">返回新闻更新</a>
      <div class="news-meta">
        <span>{escape(item["date"])}</span>
        <span>{escape(item["tag"])}</span>
        <span>{escape(_view_count_label(views))}</span>
      </div>
      <h1>{escape(item["title"])}</h1>
      <p>{escape(item["summary"])}</p>

      <h2>更新说明</h2>
{paragraphs}
{image_gallery}

      <h2>影响范围</h2>
      <ul>
{changes}
      </ul>
    </main>
    <script src="../static/code-highlight.js?v=20260617-portal-38"></script>
  </body>
</html>"""


def _cookie_secure() -> bool:
    return settings.portal_base_url.startswith("https://")


def _login_user(response: Response, user: dict) -> None:
    token = new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_IDLE_SECONDS)
    db.create_session(token, user["id"], expires_at.isoformat())
    db.mark_user_login(user["id"])
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=COOKIE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def _clear_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")


def _current_user(request: Request, extend_session: bool = True) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    user = db.get_session_user(token, SESSION_IDLE_SECONDS if extend_session else None)
    if not user:
        raise HTTPException(status_code=401, detail="登录状态已过期")
    return user


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("Authorization") or ""
    if not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    return token or None


def _agent_event_user(request: Request) -> dict:
    token = _bearer_token(request)
    if token:
        user = _user_from_api_key(token, import_external=True)
        if not user:
            raise HTTPException(status_code=401, detail="Agent 上报 Key 不正确")
        return user
    return _current_user(request, extend_session=False)


def _mask_api_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return "***"
    if api_key.startswith("sk-"):
        return f"sk-***{api_key[-4:]}"
    return f"{api_key[:3]}***{api_key[-4:]}"


def _load_catalog_or_500() -> dict[str, Any]:
    try:
        return load_catalog()
    except CatalogError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token") or request.query_params.get("admin_token")
    if not settings.portal_admin_token or token != settings.portal_admin_token:
        raise HTTPException(status_code=401, detail="后台管理 token 不正确")


def _public_user(user: dict) -> dict:
    masked_key = _mask_api_key(user["litellm_key"])
    return {
        "id": user["id"],
        "email": user["email"],
        "email_masked": _mask_email(user["email"]),
        "api_key": masked_key,
        "api_key_masked": masked_key,
        "has_api_key": bool(user["litellm_key"]),
        "key_status": user["key_status"],
        "base_url": settings.litellm_public_base_url,
        "password_configured": bool(user.get("password_hash")),
        "created_at": user["created_at"],
        "last_login_at": user.get("last_login_at"),
    }


def _active_key_info(user: dict) -> dict:
    try:
        key_info = get_key_info(user["litellm_key"])
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"Key 状态读取失败: {_redact_error(exc)}") from exc
    if key_info.get("blocked"):
        if user.get("key_status") == "active":
            db.mark_user_key_pending(user["id"])
        raise HTTPException(status_code=402, detail="当前 Key 未激活或已停用")
    if user.get("key_status") != "active":
        raise HTTPException(status_code=402, detail="当前 Key 未激活或已停用")
    max_budget = Decimal(str(key_info.get("max_budget") or "0"))
    spend = Decimal(str(key_info.get("spend") or "0"))
    if max_budget <= 0 or spend >= max_budget:
        _block_over_budget_key(user, user["litellm_key"], max_budget=max_budget, spend=spend)
        raise HTTPException(status_code=402, detail="当前 Key 余额不足，请先充值")
    return key_info


def _block_over_budget_key(user: dict, api_key: str, *, max_budget: Decimal, spend: Decimal) -> None:
    try:
        block_key(api_key)
    except LiteLLMError as exc:
        db.log_event(
            "budget",
            f"auto block failed user_id={user.get('id')} spend={spend} max_budget={max_budget}: {_redact_error(exc)}",
            user.get("email"),
        )
        return
    if user.get("id"):
        db.mark_user_key_pending(user["id"])
    db.log_event(
        "budget",
        f"auto blocked over-budget key user_id={user.get('id')} spend={spend} max_budget={max_budget}",
        user.get("email"),
    )


def _api_token_from_request(request: Request) -> str | None:
    token = _bearer_token(request)
    if token:
        return token
    for header in ("x-api-key", "anthropic-api-key"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value
    return None


def _key_info_for_api_token(api_key: str) -> dict:
    try:
        return get_key_info(api_key, auth_key=api_key)
    except LiteLLMError:
        return get_key_info(api_key)


def _budget_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "error",
            "error": {
                "type": "budget_exceeded" if status_code == 429 else "authentication_error",
                "message": message,
            },
        },
    )


def _require_active_api_key(api_key: str, payload: dict[str, Any]) -> tuple[dict, dict]:
    user = _user_from_api_key(api_key, import_external=True)
    if not user:
        raise HTTPException(status_code=401, detail="API Key 不正确")
    try:
        key_info = _key_info_for_api_token(api_key)
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"Key 状态读取失败: {_redact_error(exc)}") from exc
    if key_info.get("blocked"):
        if user.get("key_status") == "active":
            db.mark_user_key_pending(user["id"])
        raise HTTPException(status_code=402, detail="当前 Key 未激活或已停用")
    if user.get("key_status") != "active":
        raise HTTPException(status_code=402, detail="当前 Key 未激活或已停用")
    max_budget = Decimal(str(key_info.get("max_budget") or "0"))
    spend = Decimal(str(key_info.get("spend") or "0"))
    if max_budget <= 0 or spend >= max_budget:
        _block_over_budget_key(user, api_key, max_budget=max_budget, spend=spend)
        raise HTTPException(status_code=429, detail="当前 Key 余额不足，请先充值")
    model = str(payload.get("model") or "").strip()
    allowed_models = _models_from_key_info(key_info)
    if model and allowed_models and model not in allowed_models:
        raise HTTPException(status_code=403, detail="当前 Key 未订购该模型")
    return user, key_info


def _anthropic_forward_headers(request: Request, api_key: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
        "content-type": "application/json",
    }
    for name in ("accept", "anthropic-version", "anthropic-beta", "user-agent"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


def _models_from_key_info(key_info: dict) -> list[str]:
    return [model for model in (key_info.get("models") or []) if model and model != "__pending_payment__"]


def _allowed_key_models(user: dict) -> list[str]:
    return _models_from_key_info(_active_key_info(user))


def _normalize_chat_image_part(part: dict[str, Any]) -> dict[str, Any]:
    image_url = part.get("image_url")
    if isinstance(image_url, dict):
        url = str(image_url.get("url") or "").strip()
        detail = str(image_url.get("detail") or "auto").strip().lower()
    else:
        url = str(image_url or "").strip()
        detail = "auto"
    if detail not in {"auto", "low", "high"}:
        detail = "auto"
    match = CHAT_IMAGE_DATA_URL_RE.match(url)
    if not match:
        raise HTTPException(status_code=422, detail="图片必须是 data URL base64 格式")
    mime_type = match.group(1).lower()
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in CHAT_IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="图片格式仅支持 PNG、JPG、WebP、GIF")
    image_data = re.sub(r"\s+", "", match.group(2))
    try:
        raw = base64.b64decode(image_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="图片 base64 内容不正确") from exc
    if len(raw) > CHAT_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=422, detail="单张图片不能超过 5MB")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{image_data}",
            "detail": detail,
        },
    }


def _normalize_chat_content(role: str, content: Any) -> str | list[dict[str, Any]] | None:
    if isinstance(content, str):
        text = content.strip()
        if len(text) > 20000:
            raise HTTPException(status_code=422, detail="单条消息过长")
        return text or None
    if not isinstance(content, list):
        raise HTTPException(status_code=422, detail="消息内容格式不支持")

    parts: list[dict[str, Any]] = []
    text_length = 0
    image_count = 0
    for raw_part in content:
        if not isinstance(raw_part, dict):
            continue
        part_type = str(raw_part.get("type") or "").strip().lower()
        if part_type == "text":
            text = str(raw_part.get("text") or "").strip()
            if not text:
                continue
            text_length += len(text)
            if text_length > 20000:
                raise HTTPException(status_code=422, detail="单条消息过长")
            parts.append({"type": "text", "text": text})
        elif part_type == "image_url":
            if role != "user":
                raise HTTPException(status_code=422, detail="只有用户消息可以包含图片")
            image_count += 1
            if image_count > CHAT_IMAGE_MAX_COUNT:
                raise HTTPException(status_code=422, detail="单条消息最多上传 4 张图片")
            parts.append(_normalize_chat_image_part(raw_part))
    if not parts:
        return None
    if image_count == 0:
        return "\n".join(str(part.get("text") or "") for part in parts).strip() or None
    return parts


def _chat_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    if not messages:
        raise HTTPException(status_code=422, detail="消息不能为空")
    if len(messages) > 40:
        raise HTTPException(status_code=422, detail="最多保留 40 条上下文消息")
    cleaned: list[dict[str, Any]] = []
    for item in messages:
        role = item.role.strip().lower()
        if role not in {"system", "user", "assistant"}:
            raise HTTPException(status_code=422, detail="消息角色不支持")
        content = _normalize_chat_content(role, item.content)
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    if not cleaned or cleaned[-1]["role"] != "user":
        raise HTTPException(status_code=422, detail="最后一条消息必须来自用户")
    return cleaned[-40:]


def _clean_saved_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in messages[-40:]:
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            continue
        try:
            content = _normalize_chat_content(role, item.get("content"))
        except HTTPException:
            continue
        if not content:
            continue
        row: dict[str, Any] = {"role": role, "content": content}
        usage = item.get("usage")
        if isinstance(usage, dict):
            row["usage"] = {
                "prompt_tokens": _safe_int(usage.get("prompt_tokens")),
                "completion_tokens": _safe_int(usage.get("completion_tokens")),
                "total_tokens": _safe_int(usage.get("total_tokens")),
            }
        cleaned.append(row)
    return cleaned


def _public_chat_thread(row: dict) -> dict:
    try:
        messages = json.loads(row.get("messages_json") or "[]")
    except json.JSONDecodeError:
        messages = []
    try:
        last_usage = json.loads(row.get("last_usage_json") or "null")
    except json.JSONDecodeError:
        last_usage = None
    return {
        "id": row["id"],
        "title": row.get("title") or "新对话",
        "model": row.get("model") or "",
        "messages": messages if isinstance(messages, list) else [],
        "lastUsage": last_usage if isinstance(last_usage, dict) else None,
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _order_activation_spec(order: dict) -> Any:
    spec = activation_from_snapshot(order.get("activation_spec"))
    if spec:
        return spec
    plan = get_plan(order["plan_id"])
    if plan:
        return plan
    return None


def _latest_paid_spec_for_user(user: dict) -> Any:
    for order in db.get_orders_for_email(user["email"]):
        if order.get("status") != "paid":
            continue
        spec = _order_activation_spec(order)
        if spec:
            return spec
    return None


def _latest_paid_order_for_user(user: dict) -> dict | None:
    for order in db.get_orders_for_email(user["email"]):
        if order.get("status") == "paid":
            return order
    return None


def _money_decimal(value: object) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _catalog_cny_per_usd() -> Decimal:
    try:
        catalog = load_catalog()
        return Decimal(str((catalog.get("custom") or {}).get("cny_per_usd", "7.2")))
    except Exception:
        return Decimal("7.2")


def _refund_channel_fee(amount_cny: Decimal) -> Decimal:
    rate = Decimal(str(settings.refund_channel_fee_rate or "0"))
    fixed = Decimal(str(settings.refund_channel_fee_fixed_cny or "0"))
    minimum = Decimal(str(settings.refund_channel_fee_min_cny or "0"))
    if amount_cny <= 0:
        return Decimal("0.00")
    fee = (amount_cny * rate + fixed).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if minimum > 0:
        fee = max(fee, minimum.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return min(fee, amount_cny).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _order_budget_usd(order: dict) -> Decimal:
    spec = _order_activation_spec(order)
    if spec:
        return Decimal(str(spec.max_budget_usd)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return Decimal("0.000000")


def _deduct_refund_order_budget(user: dict, order: dict, quote: dict) -> dict[str, str]:
    deduction = _order_budget_usd(order)
    key_info = get_key_info(user["litellm_key"])
    current_budget = Decimal(str(key_info.get("max_budget") or "0"))
    target_budget = max(current_budget - deduction, Decimal("0")).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    metadata = dict(key_info.get("metadata") or {})
    refund_adjustments = dict(metadata.get("refund_adjustments") or {})
    if order["out_trade_no"] in refund_adjustments:
        return refund_adjustments[order["out_trade_no"]]

    adjustment = {
        "out_trade_no": order["out_trade_no"],
        "deducted_budget_usd": f"{deduction:.6f}",
        "previous_max_budget_usd": f"{current_budget:.6f}",
        "new_max_budget_usd": f"{target_budget:.6f}",
        "refund_amount_cny": quote["refund_amount_cny"],
        "manual_required": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    refund_adjustments[order["out_trade_no"]] = adjustment
    metadata["refund_adjustments"] = refund_adjustments
    metadata["latest_refund_out_trade_no"] = order["out_trade_no"]
    metadata["max_budget_usd"] = float(target_budget)
    update_key_budget(user["litellm_key"], float(target_budget), metadata=metadata)
    return adjustment


def _restore_refund_order_budget(user: dict, order: dict) -> dict[str, str]:
    quote: dict[str, Any] = {}
    if order.get("refund_quote_json"):
        try:
            quote = json.loads(order["refund_quote_json"])
        except json.JSONDecodeError:
            quote = {}
    adjustment = dict(quote.get("budget_deduction") or {})
    key_info = get_key_info(user["litellm_key"])
    current_budget = Decimal(str(key_info.get("max_budget") or "0"))
    restore_to = Decimal(str(adjustment.get("previous_max_budget_usd") or current_budget)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    metadata = dict(key_info.get("metadata") or {})
    refund_adjustments = dict(metadata.get("refund_adjustments") or {})
    refund_adjustments.pop(order["out_trade_no"], None)
    if refund_adjustments:
        metadata["refund_adjustments"] = refund_adjustments
    else:
        metadata.pop("refund_adjustments", None)
    if metadata.get("latest_refund_out_trade_no") == order["out_trade_no"]:
        metadata.pop("latest_refund_out_trade_no", None)
    metadata["max_budget_usd"] = float(restore_to)
    update_key_budget(user["litellm_key"], float(restore_to), metadata=metadata)
    return {
        "out_trade_no": order["out_trade_no"],
        "previous_max_budget_usd": f"{current_budget:.6f}",
        "restored_max_budget_usd": f"{restore_to:.6f}",
    }


def _order_spend_since_activation(order: dict, user: dict) -> tuple[Decimal, int]:
    start = _parse_dt(order.get("activated_at") or order.get("updated_at") or order.get("created_at"))
    if not start:
        start = datetime.now(LOCAL_TZ) - timedelta(days=180)
    end = datetime.now(LOCAL_TZ)
    logs = get_spend_logs(
        user["email"],
        start.strftime("%Y-%m-%d"),
        (end + timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    try:
        logs.extend(
            get_spend_logs(
                None,
                start.strftime("%Y-%m-%d"),
                (end + timedelta(days=1)).strftime("%Y-%m-%d"),
                api_key=_api_key_hash(user["litellm_key"]),
            )
        )
        logs = _dedupe_spend_logs(logs)
    except LiteLLMError:
        if not logs:
            raise
    spend = Decimal("0")
    requests = 0
    for log in logs:
        at = _parse_dt(log.get("startTime") or log.get("endTime"))
        if at and at < start:
            continue
        if at and at > end:
            continue
        spend += Decimal(str(log.get("spend") or "0"))
        requests += 1
    for row in db.get_chat_usage_for_email(
        user["email"],
        start.astimezone(timezone.utc).isoformat(),
        (end + timedelta(seconds=1)).astimezone(timezone.utc).isoformat(),
    ):
        at = _parse_dt(row.get("created_at"))
        if at and at < start:
            continue
        if at and at > end:
            continue
        spend += Decimal(str(row.get("spend_usd") or "0"))
        requests += 1
    return spend.quantize(Decimal("0.000001")), requests


def _refund_quote(order: dict, user: dict) -> dict:
    if order.get("status") == "refund_pending" and order.get("refund_quote_json"):
        try:
            quote = json.loads(order["refund_quote_json"])
        except json.JSONDecodeError:
            quote = {}
        if quote:
            quote["already_requested"] = True
            quote["contact_qr_url"] = "assets/contact-qr.png"
            quote["receipt_sample_url"] = "assets/refund-sample.png"
            quote["api_key_masked"] = _mask_api_key(user.get("litellm_key"))
            quote.setdefault("budget_deduction", {})
            quote["note"] = (
                "退款申请已提交，系统已自动扣减本订单对应额度。请联系管理员，提供订单号、API Key 和退款后台收据截图；"
                "管理员确认后会人工调整 Key 额度或禁用 Key，并通过人工流程完成退款。"
            )
            return quote

    if order.get("status") != "paid":
        raise HTTPException(status_code=409, detail="只有已支付订单可以申请退款")
    pending_refund = next((item for item in db.get_orders_for_email(user["email"]) if item.get("status") == "refund_pending"), None)
    if pending_refund:
        raise HTTPException(status_code=409, detail="已有退款申请处理中，请先联系管理员完成当前退款")
    latest = _latest_paid_order_for_user(user)
    if not latest or latest["out_trade_no"] != order["out_trade_no"]:
        raise HTTPException(status_code=409, detail="只能退款当前最新的已支付订单")

    amount_cny = _money_decimal(order["amount_cny"])
    spend_usd, request_count = _order_spend_since_activation(order, user)
    cny_per_usd = _catalog_cny_per_usd()
    token_cost_cny = (spend_usd * cny_per_usd).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    channel_fee_cny = _refund_channel_fee(amount_cny)
    refund_amount_cny = max(amount_cny - channel_fee_cny - token_cost_cny, Decimal("0.00")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "out_trade_no": order["out_trade_no"],
        "amount_cny": f"{amount_cny:.2f}",
        "channel_fee_cny": f"{channel_fee_cny:.2f}",
        "channel_fee_rate": str(settings.refund_channel_fee_rate),
        "token_cost_usd": f"{spend_usd:.6f}",
        "token_cost_cny": f"{token_cost_cny:.2f}",
        "cny_per_usd": f"{cny_per_usd:.2f}",
        "refund_amount_cny": f"{refund_amount_cny:.2f}",
        "usage_request_count": request_count,
        "usage_started_at": order.get("activated_at") or order.get("updated_at") or order.get("created_at"),
        "manual_required": True,
        "already_requested": False,
        "contact_qr_url": "assets/contact-qr.png",
        "receipt_sample_url": "assets/refund-sample.png",
        "api_key_masked": _mask_api_key(user.get("litellm_key")),
        "note": (
            "确认后会先自动扣减本订单对应额度，但不会自动打款。"
            "请联系管理员，提供订单号、API Key 和退款后台收据截图；"
            "管理员确认后会人工调整 Key 额度或禁用 Key，并通过人工流程完成退款。"
        ),
    }


def _public_order(order: dict) -> dict:
    user = db.get_user(order["user_id"])
    spec = activation_from_snapshot(order.get("plan_snapshot")) or _order_activation_spec(order)
    masked_key = _mask_api_key(user["litellm_key"] if user else None)
    email = user["email"] if user else None
    return {
        "out_trade_no": order["out_trade_no"],
        "email": email,
        "email_masked": _mask_email(email),
        "plan": spec.public_dict() if spec else {"id": order["plan_id"], "name": order["plan_id"]},
        "order_type": order.get("order_type") or "plan",
        "amount_cny": order["amount_cny"],
        "pay_type": order["pay_type"],
        "pay_type_label": PAY_TYPES.get(order["pay_type"], order["pay_type"]),
        "status": order["status"],
        "payment_url": order["payment_url"],
        "trade_no": order["trade_no"],
        "activated_at": order["activated_at"],
        "email_status": order["email_status"],
        "created_at": order["created_at"],
        "updated_at": order["updated_at"],
        "refund": {
            "channel_fee_cny": order.get("refund_channel_fee_cny"),
            "token_cost_cny": order.get("refund_token_cost_cny"),
            "token_cost_usd": order.get("refund_token_cost_usd"),
            "amount_cny": order.get("refund_amount_cny"),
            "requested_at": order.get("refund_requested_at"),
        },
        "api_key": masked_key,
        "api_key_masked": masked_key,
        "has_api_key": bool(user and user["litellm_key"]),
        "key_status": user["key_status"] if user else None,
        "base_url": settings.litellm_public_base_url,
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _empty_usage_bucket(label: str) -> dict:
    return {
        "label": label,
        "spend": 0.0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "requests": 0,
    }


def _round_money(value: float) -> float:
    return round(value, 6)


def _redact_error(value: Exception | str) -> str:
    text = str(value)
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", text)
    text = re.sub(r"key=([^&\s]+)", "key=***", text)
    return text


def _api_key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _spend_log_model(log: dict[str, Any]) -> str:
    return log.get("model") or log.get("model_group") or log.get("model_id") or "unknown"


def _spend_log_identity(log: dict[str, Any]) -> tuple[Any, ...]:
    request_id = str(log.get("request_id") or "").strip()
    if request_id:
        return ("request_id", request_id)
    return (
        "fields",
        log.get("api_key") or log.get("key_alias") or log.get("api_key_alias") or "",
        log.get("startTime") or log.get("endTime") or "",
        _spend_log_model(log),
        str(log.get("spend") or ""),
        str(log.get("total_tokens") or ""),
        str(log.get("prompt_tokens") or ""),
        str(log.get("completion_tokens") or ""),
    )


def _spend_log_score(log: dict[str, Any]) -> tuple[bool, bool, bool]:
    return (
        _safe_float(log.get("spend")) > 0,
        _safe_int(log.get("total_tokens")) > 0,
        _spend_log_model(log) != "unknown",
    )


def _dedupe_spend_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[tuple[Any, ...]] = []
    by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for log in logs:
        identity = _spend_log_identity(log)
        if identity not in by_identity:
            order.append(identity)
            by_identity[identity] = log
            continue
        if _spend_log_score(log) > _spend_log_score(by_identity[identity]):
            by_identity[identity] = log
    return [by_identity[identity] for identity in order]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clamp_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(100.0, value)), 1)


def _usage_to_percent(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    if 0 <= numeric <= 1:
        numeric *= 100
    return _clamp_percent(numeric)


def _percent_value(value: Any) -> float | None:
    return _clamp_percent(_as_float(value))


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_is_empty(window: dict[str, Any]) -> bool:
    remaining = _as_float(window.get("remaining_percent"))
    if remaining is not None:
        return remaining <= 0
    status = str(window.get("status") or "").lower()
    return bool(window.get("limit_reached")) or status in {
        "limited",
        "exhausted",
        "rate_limited",
        "blocked",
        "quota_exhausted",
    }


def _claude_window(window: dict[str, Any] | None) -> dict[str, Any]:
    window = window or {}
    used_percent = _usage_to_percent(window.get("utilization"))
    status = str(window.get("status") or "unknown").lower()
    blocking_statuses = {"limited", "exhausted", "rate_limited", "blocked", "quota_exhausted"}
    if used_percent is None and status in blocking_statuses:
        used_percent = 100.0
    remaining_percent = _clamp_percent(100 - used_percent) if used_percent is not None else None
    limit_reached = remaining_percent == 0 if remaining_percent is not None else status in blocking_statuses
    return {
        "status": status,
        "used_percent": used_percent,
        "remaining_percent": remaining_percent,
        "reset_at": window.get("reset_at"),
        "limit_reached": limit_reached,
    }


def _codex_account_window(account: dict[str, Any], quota_key: str) -> dict[str, Any]:
    quota = ((account.get("quota") or {}).get(quota_key) or {})
    limit_reached = quota.get("limit_reached") is True
    used_percent = _percent_value(quota.get("used_percent"))
    if used_percent is None and limit_reached:
        used_percent = 100.0
    remaining_percent = _clamp_percent(100 - used_percent) if used_percent is not None else None
    return {
        "status": "limited" if limit_reached else "allowed",
        "used_percent": used_percent,
        "remaining_percent": remaining_percent,
        "reset_at": quota.get("reset_at"),
        "limit_reached": limit_reached,
    }


def _aggregate_resource_windows(windows: list[dict[str, Any]], *, sum_capacity: bool = False) -> dict[str, Any]:
    known = [window for window in windows if window.get("remaining_percent") is not None]
    if not windows:
        return {
            "status": "unknown",
            "used_percent": None,
            "remaining_percent": None,
            "reset_at": None,
            "available_accounts": 0,
            "total_accounts": 0,
            "limit_reached": False,
        }
    if not known:
        return {
            "status": "unknown",
            "used_percent": None,
            "remaining_percent": None,
            "reset_at": None,
            "available_accounts": 0,
            "total_accounts": len(windows),
            "limit_reached": any(window.get("limit_reached") for window in windows),
        }

    remaining_values = [_as_float(window.get("remaining_percent")) or 0.0 for window in known]
    used_values = [_as_float(window.get("used_percent")) or 0.0 for window in known]
    remaining_percent = round(sum(remaining_values), 1) if sum_capacity else _clamp_percent(sum(remaining_values) / len(remaining_values))
    used_percent = round(sum(used_values), 1) if sum_capacity else _clamp_percent(100 - remaining_percent)
    available_accounts = sum(1 for window in known if (_as_float(window.get("remaining_percent")) or 0) > 0)
    exhausted_reset_times = [
        parsed
        for parsed in (_parse_iso_datetime(window.get("reset_at")) for window in windows if _window_is_empty(window))
        if parsed
    ]
    reset_at = _iso_datetime(min(exhausted_reset_times)) if exhausted_reset_times else None
    return {
        "status": "allowed" if available_accounts else "limited",
        "used_percent": used_percent,
        "remaining_percent": remaining_percent,
        "reset_at": reset_at,
        "available_accounts": available_accounts,
        "total_accounts": len(windows),
        "limit_reached": available_accounts == 0,
    }


def _windows_have_capacity(*windows: dict[str, Any]) -> bool:
    known = [_as_float(window.get("remaining_percent")) for window in windows]
    known = [value for value in known if value is not None]
    return bool(known) and all(value > 0 for value in known)


def _windows_not_blocking(*windows: dict[str, Any]) -> bool:
    known = [_as_float(window.get("remaining_percent")) for window in windows]
    known = [value for value in known if value is not None]
    if known:
        return all(value > 0 for value in known)
    return not any(_window_is_empty(window) for window in windows)


def _usable_count(service: dict[str, Any], accounts: list[dict[str, Any]]) -> int:
    summary = service.get("summary") or {}
    pool = service.get("pool") or {}
    value = _first_present(summary.get("usable"), pool.get("active"))
    if value is not None:
        return int(value or 0)
    return sum(1 for account in accounts if account.get("usable"))


def _claude_available(service: dict[str, Any], five_hour: dict[str, Any], seven_day: dict[str, Any]) -> bool:
    if service.get("reachable") is False or service.get("authenticated") is False:
        return False
    if service.get("service_state") in {"error", "down", "unavailable"}:
        return False
    if service.get("refresh_ok") is False:
        return False
    if _usable_count(service, service.get("accounts") or []) <= 0:
        return False
    return _windows_not_blocking(five_hour, seven_day)


def _latest_required_reset(windows: list[dict[str, Any]]) -> str | None:
    resets = [
        parsed
        for parsed in (_parse_iso_datetime(window.get("reset_at")) for window in windows if _window_is_empty(window))
        if parsed
    ]
    return _iso_datetime(max(resets)) if resets else None


def _codex_next_available_at(accounts: list[dict[str, Any]]) -> str | None:
    ready_times: list[datetime] = []
    for account in accounts:
        blocking_resets = []
        for quota_key in ("rate_limit", "secondary_rate_limit"):
            window = _codex_account_window(account, quota_key)
            if _window_is_empty(window):
                parsed = _parse_iso_datetime(window.get("reset_at"))
                if parsed:
                    blocking_resets.append(parsed)
        if not blocking_resets:
            return None
        ready_times.append(max(blocking_resets))
    return _iso_datetime(min(ready_times)) if ready_times else None


def _sum_account_usage(accounts: list[dict[str, Any]], key: str) -> int:
    total = 0
    for account in accounts:
        total += _as_int((account.get("usage") or {}).get(key)) or 0
    return total


def _service_summary_counts(service: dict[str, Any], fallback_total: int = 0) -> tuple[int, int]:
    summary = service.get("summary") or {}
    pool = service.get("pool") or {}
    total = _first_present(summary.get("total"), pool.get("total"), service.get("account_count"), fallback_total, 0)
    usable = _first_present(summary.get("usable"), pool.get("active"), 0)
    return int(total or 0), int(usable or 0)


def _first_account_reason(accounts: list[dict[str, Any]]) -> str | None:
    for account in accounts:
        reason = account.get("reason") or account.get("state") or account.get("status")
        if reason:
            return str(reason)
    return None


def _public_resource_service(service_name: str, service: dict[str, Any]) -> dict[str, Any]:
    accounts = service.get("accounts") or []
    service_state = service.get("service_state") or "unknown"

    if service_name == "claude":
        active_window = service.get("active_window") or {}
        five_hour = _claude_window(active_window.get("five_hour"))
        seven_day = _claude_window(active_window.get("seven_day"))
        next_available_at = _latest_required_reset([five_hour, seven_day])
        request_count = _sum_account_usage(accounts, "request_count")
        input_tokens = _sum_account_usage(accounts, "input_tokens")
        output_tokens = _sum_account_usage(accounts, "output_tokens")
        last_model = next(((account.get("usage") or {}).get("last_model") for account in accounts if (account.get("usage") or {}).get("last_model")), None)
        available = _claude_available(service, five_hour, seven_day)
        state_reason = None if available else _first_account_reason(accounts)
    else:
        five_windows = [_codex_account_window(account, "rate_limit") for account in accounts]
        seven_windows = [_codex_account_window(account, "secondary_rate_limit") for account in accounts]
        five_hour = _aggregate_resource_windows(five_windows, sum_capacity=True)
        seven_day = _aggregate_resource_windows(seven_windows, sum_capacity=True)
        for window in (five_hour, seven_day):
            window.pop("available_accounts", None)
            window.pop("total_accounts", None)
        next_available_at = _codex_next_available_at(accounts)
        usage_summary = service.get("usage_summary") or {}
        request_count = _first_present(usage_summary.get("total_request_count"), _sum_account_usage(accounts, "request_count"))
        input_tokens = _first_present(usage_summary.get("total_input_tokens"), _sum_account_usage(accounts, "input_tokens"))
        output_tokens = _first_present(usage_summary.get("total_output_tokens"), _sum_account_usage(accounts, "output_tokens"))
        last_model = next(((account.get("usage") or {}).get("last_model") for account in accounts if (account.get("usage") or {}).get("last_model")), None)
        available = (
            bool(service.get("reachable"))
            and bool(service.get("authenticated"))
            and service.get("service_state") not in {"error", "down", "unavailable"}
            and _usable_count(service, accounts) > 0
            and _windows_have_capacity(five_hour, seven_day)
        )
        state_reason = None if available else _first_account_reason([account for account in accounts if not account.get("usable")])

    return {
        "service": service_name,
        "label": "Claude" if service_name == "claude" else "Codex",
        "checked_at": service.get("checked_at"),
        "reachable": service.get("reachable") if isinstance(service.get("reachable"), bool) else None,
        "authenticated": service.get("authenticated") if isinstance(service.get("authenticated"), bool) else None,
        "routing_mode": service.get("routing_mode") or "-",
        "service_state": service_state,
        "available": available,
        "windows": {
            "five_hour": five_hour,
            "seven_day": seven_day,
        },
        "next_available_at": next_available_at,
        "request_count": int(request_count or 0),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "last_model": last_model,
        "state_reason": state_reason,
    }


def _public_resource_status(payload: dict[str, Any]) -> dict[str, Any]:
    services = payload.get("services") or {}
    return {
        "ok": bool(payload.get("ok")),
        "generated_at": payload.get("generated_at"),
        "services": {
            name: _public_resource_service(name, services.get(name) or {})
            for name in ("claude", "codex")
            if name in services
        },
    }


SECRET_FIELD_RE = re.compile(
    r"(api[_-]?key|auth|authorization|password|secret|access[_-]?token|refresh[_-]?token|bearer)",
    re.IGNORECASE,
)


def _truncate_text(value: object, limit: int = 1200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else f"{text[:limit]}..."


def _payload_get(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _nested_payload_get(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if current not in (None, "") else None


def _int_payload_get(payload: dict[str, Any], *keys: str) -> int | None:
    value = _payload_get(payload, *keys)
    if value is None:
        return None
    parsed = _safe_int(value)
    return parsed if parsed > 0 else None


def _int_header_get(request: Request, *headers: str) -> int | None:
    for header in headers:
        parsed = _safe_int(request.headers.get(header))
        if parsed > 0:
            return parsed
    return None


def _redact_payload(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_FIELD_RE.search(key_text):
                redacted[key_text] = "***"
            else:
                redacted[key_text] = _redact_payload(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item, depth + 1) for item in value[:80]]
    if isinstance(value, str):
        text = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", value)
        text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1***", text, flags=re.IGNORECASE)
        return _truncate_text(text, 2400)
    return value


def _agent_event_status(event_type: str, payload_status: str | None = None) -> tuple[str, str]:
    if payload_status:
        normalized = payload_status.strip().lower().replace(" ", "_")
        return normalized, payload_status
    event = event_type.lower()
    if event in {"userpromptsubmit", "user_prompt_submit", "sessionstart", "start"}:
        return "running", "收到用户问题"
    if event in {"pretooluse", "pre_tool_use", "tool_start", "toolstart"}:
        return "tool_running", "正在执行工具"
    if event in {"posttooluse", "post_tool_use", "tool_done", "toolend", "tool_end"}:
        return "tool_done", "工具执行完成"
    if event in {"precompact", "pre_compact"}:
        return "compacting", "正在压缩上下文"
    if event in {"postcompact", "post_compact"}:
        return "running", "上下文压缩完成"
    if event in {"subagentstart", "subagent_start"}:
        return "subagent_running", "子任务执行中"
    if event in {"stop", "sessionstop", "session_stop", "done", "completed", "finish", "finished"}:
        return "completed", "任务完成"
    if event in {"error", "failed", "exception"}:
        return "failed", "任务失败"
    return "running", event_type


def _normalize_agent_payload(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    client = _truncate_text(_payload_get(payload, "client", "agent", "source"), 40) or request.headers.get("X-Agent-Client") or "agent"
    client = client.lower().replace(" ", "-")
    event_type = (
        _truncate_text(_payload_get(payload, "event_type", "event", "hook_event_name", "type"), 80)
        or request.headers.get("X-Agent-Event")
        or "event"
    )
    cwd = _truncate_text(_payload_get(payload, "cwd", "working_directory", "workdir"), 500)
    session_id = (
        _truncate_text(
            _payload_get(payload, "session_id", "conversation_id", "thread_id", "transcript_path", "run_id"),
            240,
        )
        or request.headers.get("X-Agent-Session-Id")
        or f"{client}:{cwd or 'default'}:{datetime.now(LOCAL_TZ).strftime('%Y%m%d')}"
    )
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool_name = _truncate_text(
        _payload_get(payload, "tool_name", "tool", "name", "current_tool")
        or _nested_payload_get(payload, "tool", "name")
        or tool_input.get("command"),
        220,
    )
    first_prompt = _truncate_text(
        _payload_get(payload, "first_prompt", "prompt", "user_prompt", "message", "input"),
        2000,
    )
    status, default_step = _agent_event_status(event_type, _truncate_text(payload.get("status"), 80))
    current_step = _truncate_text(_payload_get(payload, "current_step", "step", "message"), 500) or default_step
    used_tokens = _int_payload_get(payload, "used_tokens", "total_tokens", "token_count", "tokens_used", "context_tokens")
    if used_tokens is None:
        used_tokens = _int_header_get(request, "X-Agent-Used-Tokens", "X-Agent-Total-Tokens")
    if used_tokens is None and isinstance(payload.get("usage"), dict):
        used_tokens = _int_payload_get(payload["usage"], "total_tokens", "used_tokens", "input_tokens")
    context_window = _int_payload_get(payload, "context_window", "model_context_window", "max_context_tokens")
    if context_window is None:
        context_window = _int_header_get(request, "X-Agent-Context-Window", "X-Agent-Max-Context-Tokens")
    remaining_context = _int_payload_get(payload, "remaining_context", "remaining_context_window", "context_remaining")
    if remaining_context is None:
        remaining_context = _int_header_get(request, "X-Agent-Remaining-Context", "X-Agent-Context-Remaining")
    if remaining_context is None and context_window is not None and used_tokens is not None:
        remaining_context = max(context_window - used_tokens, 0)
    event_lower = event_type.lower()
    finished = status in {"completed", "failed", "cancelled"} or event_lower in {
        "stop",
        "sessionstop",
        "session_stop",
        "done",
        "completed",
        "finish",
        "finished",
        "error",
        "failed",
    }
    return {
        "client": client,
        "session_id": session_id,
        "event_type": event_type,
        "first_prompt": first_prompt,
        "status": status,
        "current_tool": tool_name,
        "current_step": current_step,
        "model": _truncate_text(_payload_get(payload, "model", "model_id"), 120)
        or _truncate_text(request.headers.get("X-Agent-Model"), 120),
        "context_window": context_window,
        "used_tokens": used_tokens,
        "remaining_context": remaining_context,
        "cwd": cwd,
        "finished": finished,
    }


def _public_agent_session(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("status") or "running"
    last_seen = _parse_dt(row.get("last_seen_at"))
    if status in {"running", "tool_running", "tool_done", "compacting", "subagent_running"}:
        if last_seen and datetime.now(timezone.utc) - last_seen > timedelta(minutes=2):
            status = "stale"
    return {
        "id": row["id"],
        "client": row.get("client") or "agent",
        "session_id": row.get("session_id"),
        "first_prompt": row.get("first_prompt") or "-",
        "status": status,
        "current_tool": row.get("current_tool") or "-",
        "current_step": row.get("current_step") or "-",
        "model": row.get("model") or "-",
        "context_window": row.get("context_window"),
        "used_tokens": row.get("used_tokens"),
        "remaining_context": row.get("remaining_context"),
        "cwd": row.get("cwd") or "-",
        "started_at": row.get("started_at"),
        "last_seen_at": row.get("last_seen_at"),
        "finished_at": row.get("finished_at"),
        "event_count": row.get("event_count") or 0,
    }


def _public_agent_event(row: dict[str, Any]) -> dict[str, Any]:
    payload: Any = None
    if row.get("payload_json"):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = row["payload_json"]
    return {
        "id": row["id"],
        "event_type": row.get("event_type") or "event",
        "payload": payload,
        "created_at": row.get("created_at"),
    }


def _model_info_map(rows: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        name = row.get("model_name")
        if not name:
            continue
        result[name] = row.get("model_info") or {}
    return result


def _cached_model_info_map() -> dict[str, dict]:
    global _MODEL_INFO_CACHE
    cached_at, cached = _MODEL_INFO_CACHE
    now = time.monotonic()
    if cached and now - cached_at < MODEL_INFO_CACHE_TTL_SECONDS:
        return cached
    fresh = _model_info_map(get_model_info())
    _MODEL_INFO_CACHE = (now, fresh)
    return fresh


def _model_cost_rates(model: str) -> tuple[Decimal, Decimal]:
    info: dict[str, Any] = {}
    try:
        info.update(catalog_model_map(enabled_only=False).get(model, {}) or {})
    except CatalogError:
        pass
    try:
        info.update(_cached_model_info_map().get(model, {}) or {})
    except LiteLLMError:
        pass
    input_cost = Decimal(str(info.get("input_cost_per_token") or "0"))
    output_cost = Decimal(str(info.get("output_cost_per_token") or "0"))
    return input_cost, output_cost


def _chat_cost_usd(model: str, usage: dict) -> Decimal:
    prompt_tokens = _safe_int(usage.get("prompt_tokens"))
    completion_tokens = _safe_int(usage.get("completion_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    if total_tokens and not prompt_tokens and not completion_tokens:
        completion_tokens = total_tokens
    input_cost, output_cost = _model_cost_rates(model)
    cost = Decimal(prompt_tokens) * input_cost + Decimal(completion_tokens) * output_cost
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _record_chat_accounting(user: dict, key_info: dict, model: str, usage: dict, request_id: str | None) -> Decimal:
    spend = _chat_cost_usd(model, usage)
    prompt_tokens = _safe_int(usage.get("prompt_tokens"))
    completion_tokens = _safe_int(usage.get("completion_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens")) or prompt_tokens + completion_tokens
    db.record_chat_usage(
        user["id"],
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        spend_usd=f"{spend:.6f}",
        request_id=request_id,
    )
    if spend > 0:
        latest_info = get_key_info(user["litellm_key"])
        current_spend = Decimal(str(latest_info.get("spend") or key_info.get("spend") or "0"))
        metadata = dict(latest_info.get("metadata") or {})
        metadata["portal_chat_last_charged_at"] = datetime.now(timezone.utc).isoformat()
        metadata["portal_chat_last_request_id"] = request_id or ""
        metadata["portal_chat_last_model"] = model
        update_key_spend(user["litellm_key"], float(current_spend + spend), metadata=metadata)
    return spend


def _build_usage(user: dict) -> dict:
    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=180)
    errors: list[str] = []

    try:
        key_info = get_key_info(user["litellm_key"])
    except LiteLLMError as admin_exc:
        try:
            key_info = get_key_info(user["litellm_key"], auth_key=user["litellm_key"])
        except LiteLLMError as user_exc:
            key_info = {}
            errors.append(f"key_info: admin={_redact_error(admin_exc)}; user={_redact_error(user_exc)}")

    try:
        model_info = _cached_model_info_map()
    except LiteLLMError as exc:
        model_info = {}
        errors.append(f"model_info: {_redact_error(exc)}")
    try:
        configured_model_info = catalog_model_map(enabled_only=False)
    except CatalogError as exc:
        configured_model_info = {}
        errors.append(f"catalog: {_redact_error(exc)}")

    logs: list[dict[str, Any]] = []
    user_logs: list[dict[str, Any]] = []
    start_date = start.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")
    try:
        user_logs = get_spend_logs(user["email"], start_date, end_date)
        logs.extend(user_logs)
    except LiteLLMError as exc:
        errors.append(f"spend_logs[user_id]: {_redact_error(exc)}")

    litellm_user_id = str(key_info.get("user_id") or "").strip()
    should_query_key_logs = litellm_user_id != user["email"] or (not user_logs and _safe_float(key_info.get("spend")) > 0)
    if should_query_key_logs:
        try:
            logs.extend(
                get_spend_logs(
                    None,
                    start_date,
                    end_date,
                    max_pages=API_KEY_USAGE_LOG_MAX_PAGES,
                    api_key=_api_key_hash(user["litellm_key"]),
                )
            )
        except LiteLLMError as exc:
            errors.append(f"spend_logs[api_key]: {_redact_error(exc)}")
    try:
        chat_rows = db.get_chat_usage_for_email(user["email"], start.isoformat(), end.isoformat())
        logs.extend(
            {
                "startTime": row["created_at"],
                "endTime": row["created_at"],
                "model": row["model"],
                "spend": row["spend_usd"],
                "total_tokens": row["total_tokens"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "request_id": row.get("request_id"),
                "source": "portal-chat",
            }
            for row in chat_rows
        )
    except Exception as exc:
        errors.append(f"chat_usage: {_redact_error(exc)}")
    logs = _dedupe_spend_logs(logs)

    daily: dict[str, dict] = {}
    weekly: dict[str, dict] = {}
    monthly: dict[str, dict] = {}
    by_model: dict[str, dict] = defaultdict(
        lambda: {
            "model": "",
            "spend": 0.0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "requests": 0,
        }
    )
    recent_logs: list[dict] = []

    for log in logs:
        at = _parse_dt(log.get("startTime") or log.get("endTime"))
        if not at:
            continue
        spend = _safe_float(log.get("spend"))
        total_tokens = _safe_int(log.get("total_tokens"))
        prompt_tokens = _safe_int(log.get("prompt_tokens"))
        completion_tokens = _safe_int(log.get("completion_tokens"))
        model = _spend_log_model(log)

        labels = {
            "daily": at.strftime("%Y-%m-%d"),
            "weekly": f"{at.isocalendar().year}-W{at.isocalendar().week:02d}",
            "monthly": at.strftime("%Y-%m"),
        }
        for target, label in ((daily, labels["daily"]), (weekly, labels["weekly"]), (monthly, labels["monthly"])):
            bucket = target.setdefault(label, _empty_usage_bucket(label))
            bucket["spend"] += spend
            bucket["total_tokens"] += total_tokens
            bucket["prompt_tokens"] += prompt_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["requests"] += 1

        model_bucket = by_model[model]
        model_bucket["model"] = model
        model_bucket["spend"] += spend
        model_bucket["total_tokens"] += total_tokens
        model_bucket["prompt_tokens"] += prompt_tokens
        model_bucket["completion_tokens"] += completion_tokens
        model_bucket["requests"] += 1

        recent_logs.append(
            {
                "time": at.strftime("%Y-%m-%d %H:%M"),
                "model": model,
                "spend": _round_money(spend),
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "request_id": log.get("request_id"),
            }
        )

    for buckets in (daily, weekly, monthly):
        for bucket in buckets.values():
            bucket["spend"] = _round_money(bucket["spend"])

    recent_logs.sort(key=lambda item: item["time"], reverse=True)

    key_spend = _safe_float(key_info.get("spend"))
    max_budget = _safe_float(key_info.get("max_budget"))
    remaining_budget = max(max_budget - key_spend, 0.0)
    allowed_models = [
        model for model in (key_info.get("models") or []) if model and model != "__pending_payment__"
    ]
    if not allowed_models and user.get("key_status") == "active":
        allowed_models = sorted(by_model)

    metadata = _safe_dict(key_info.get("metadata"))
    request_limits = _safe_dict(metadata.get("request_limits"))
    rate_limits = _safe_dict(metadata.get("rate_limits"))
    if not request_limits:
        fallback_spec = _latest_paid_spec_for_user(user)
        if fallback_spec:
            request_limits = {
                "request_tier": fallback_spec.request_tier,
                "total_request_limit": fallback_spec.total_request_limit,
                "five_hour_request_limit": fallback_spec.five_hour_request_limit,
                "daily_request_limit": fallback_spec.daily_request_limit,
                "weekly_request_limit": fallback_spec.weekly_request_limit,
                "monthly_request_limit": fallback_spec.monthly_request_limit,
            }
            if not rate_limits:
                rate_limits = {
                    "rpm_tier": fallback_spec.rpm_tier,
                    "tpm_tier": fallback_spec.tpm_tier,
                }
    model_rows = []
    for model in sorted(set(allowed_models) | set(by_model.keys())):
        info = {
            **(configured_model_info.get(model, {}) or {}),
            **(model_info.get(model, {}) or {}),
        }
        usage = by_model.get(model, {"spend": 0.0, "total_tokens": 0, "requests": 0})
        input_cost = _safe_float(info.get("input_cost_per_token"))
        output_cost = _safe_float(info.get("output_cost_per_token"))
        estimated_remaining_output_tokens = (
            int(remaining_budget / output_cost) if remaining_budget > 0 and output_cost > 0 else None
        )
        model_rows.append(
            {
                "model": model,
                "available": model in allowed_models,
                "max_tokens": info.get("max_tokens"),
                "max_input_tokens": info.get("max_input_tokens"),
                "max_output_tokens": info.get("max_output_tokens"),
                "input_cost_per_million_tokens": _round_money(input_cost * 1_000_000),
                "output_cost_per_million_tokens": _round_money(output_cost * 1_000_000),
                "rpm_limit": key_info.get("rpm_limit"),
                "tpm_limit": key_info.get("tpm_limit"),
                "total_request_limit": request_limits.get("total_request_limit"),
                "five_hour_request_limit": request_limits.get("five_hour_request_limit"),
                "weekly_request_limit": request_limits.get("weekly_request_limit"),
                "rpm_tier": rate_limits.get("rpm_tier"),
                "tpm_tier": rate_limits.get("tpm_tier"),
                "estimated_remaining_output_tokens": estimated_remaining_output_tokens,
                "spend": _round_money(_safe_float(usage.get("spend"))),
                "total_tokens": _safe_int(usage.get("total_tokens")),
                "requests": _safe_int(usage.get("requests")),
            }
        )

    total_from_logs = {
        "spend": _round_money(sum(_safe_float(item.get("spend")) for item in logs)),
        "total_tokens": sum(_safe_int(item.get("total_tokens")) for item in logs),
        "prompt_tokens": sum(_safe_int(item.get("prompt_tokens")) for item in logs),
        "completion_tokens": sum(_safe_int(item.get("completion_tokens")) for item in logs),
        "requests": len(logs),
    }
    today_label = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    now_local = datetime.now(LOCAL_TZ)
    five_hours_ago = now_local - timedelta(hours=5)
    month_label = datetime.now(LOCAL_TZ).strftime("%Y-%m")
    week_label = f"{now_local.isocalendar().year}-W{now_local.isocalendar().week:02d}"
    five_hour_requests = 0
    for log in logs:
        at = _parse_dt(log.get("startTime") or log.get("endTime"))
        if at and at >= five_hours_ago:
            five_hour_requests += 1
    today_requests = _safe_int(daily.get(today_label, {}).get("requests"))
    week_requests = _safe_int(weekly.get(week_label, {}).get("requests"))
    month_requests = _safe_int(monthly.get(month_label, {}).get("requests"))
    total_request_limit = _safe_int(request_limits.get("total_request_limit"))
    five_hour_request_limit = _safe_int(request_limits.get("five_hour_request_limit"))
    daily_request_limit = _safe_int(request_limits.get("daily_request_limit"))
    weekly_request_limit = _safe_int(request_limits.get("weekly_request_limit"))
    monthly_request_limit = _safe_int(request_limits.get("monthly_request_limit"))

    return {
        "budget": {
            "spend": _round_money(key_spend),
            "max_budget": _round_money(max_budget),
            "remaining_budget": _round_money(remaining_budget),
            "used_percent": round((key_spend / max_budget) * 100, 2) if max_budget > 0 else 0,
            "currency": "USD",
        },
        "limits": {
            "rpm_limit": key_info.get("rpm_limit"),
            "tpm_limit": key_info.get("tpm_limit"),
            "rpm_tier": rate_limits.get("rpm_tier"),
            "tpm_tier": rate_limits.get("tpm_tier"),
            "request_tier": request_limits.get("request_tier"),
            "total_request_limit": total_request_limit or None,
            "five_hour_request_limit": five_hour_request_limit or None,
            "daily_request_limit": daily_request_limit or None,
            "weekly_request_limit": weekly_request_limit or None,
            "monthly_request_limit": monthly_request_limit or None,
            "requests_used_total": total_from_logs["requests"],
            "requests_used_five_hour": five_hour_requests,
            "requests_used_today": today_requests,
            "requests_used_week": week_requests,
            "requests_used_month": month_requests,
            "requests_remaining_total": max(total_request_limit - total_from_logs["requests"], 0)
            if total_request_limit
            else None,
            "requests_remaining_five_hour": max(five_hour_request_limit - five_hour_requests, 0)
            if five_hour_request_limit
            else None,
            "requests_remaining_today": max(daily_request_limit - today_requests, 0) if daily_request_limit else None,
            "requests_remaining_week": max(weekly_request_limit - week_requests, 0) if weekly_request_limit else None,
            "requests_remaining_month": max(monthly_request_limit - month_requests, 0)
            if monthly_request_limit
            else None,
            "request_limit_enforced": False,
            "blocked": bool(key_info.get("blocked")),
            "expires": key_info.get("expires"),
        },
        "models": model_rows,
        "totals": total_from_logs,
        "daily": [daily[key] for key in sorted(daily)],
        "weekly": [weekly[key] for key in sorted(weekly)],
        "monthly": [monthly[key] for key in sorted(monthly)],
        "by_model": sorted(
            [
                {
                    **value,
                    "spend": _round_money(_safe_float(value.get("spend"))),
                }
                for value in by_model.values()
            ],
            key=lambda item: item["spend"],
            reverse=True,
        ),
        "recent_logs": recent_logs[:40],
        "errors": errors,
        "refreshed_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _empty_usage() -> dict:
    return {
        "budget": {
            "spend": 0.0,
            "max_budget": 0.0,
            "remaining_budget": 0.0,
            "used_percent": 0,
            "currency": "USD",
        },
        "limits": {
            "rpm_limit": None,
            "tpm_limit": None,
            "rpm_tier": None,
            "tpm_tier": None,
            "request_tier": None,
            "total_request_limit": None,
            "five_hour_request_limit": None,
            "daily_request_limit": None,
            "weekly_request_limit": None,
            "monthly_request_limit": None,
            "requests_used_total": 0,
            "requests_used_five_hour": 0,
            "requests_used_today": 0,
            "requests_used_week": 0,
            "requests_used_month": 0,
            "requests_remaining_total": None,
            "requests_remaining_five_hour": None,
            "requests_remaining_today": None,
            "requests_remaining_week": None,
            "requests_remaining_month": None,
            "request_limit_enforced": False,
            "blocked": False,
            "expires": None,
        },
        "models": [],
        "totals": {
            "spend": 0.0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "requests": 0,
        },
        "daily": [],
        "weekly": [],
        "monthly": [],
        "by_model": [],
        "recent_logs": [],
        "errors": [],
        "refreshed_at": "",
    }


def _usage_with_notice(usage: dict, message: str) -> dict:
    result = dict(usage)
    errors = list(result.get("errors") or [])
    if message not in errors:
        errors.append(message)
    result["errors"] = errors
    return result


def _cached_usage(user: dict, *, force: bool = False) -> dict:
    user_id = int(user["id"])
    now = time.monotonic()
    with _USAGE_LOCK:
        cached = _USAGE_CACHE.get(user_id)
        if cached and not force and now - cached[0] < USAGE_CACHE_TTL_SECONDS:
            return cached[1]
        if user_id in _USAGE_INFLIGHT:
            fallback = cached[1] if cached else _empty_usage()
            return _usage_with_notice(fallback, "用量正在刷新，请稍后再试")
        _USAGE_INFLIGHT.add(user_id)
    try:
        usage = _build_usage(user)
    except Exception as exc:
        fallback = cached[1] if cached else _empty_usage()
        usage = _usage_with_notice(fallback, f"usage: {_redact_error(exc)}")
    finally:
        with _USAGE_LOCK:
            _USAGE_INFLIGHT.discard(user_id)
    with _USAGE_LOCK:
        _USAGE_CACHE[user_id] = (time.monotonic(), usage)
    return usage


def _dashboard(user: dict, include_usage: bool = False) -> dict:
    orders = db.get_orders_for_email(user["email"])
    catalog = public_catalog()
    return {
        "user": _public_user(user),
        "orders": [_public_order(order) for order in orders],
        "usage": _cached_usage(user) if include_usage else _empty_usage(),
        "plans": catalog["plans"],
        "catalog": catalog,
        "pay_types": [{"id": key, "name": value} for key, value in PAY_TYPES.items()],
        "zpay_configured": zpay_is_configured(),
    }


def _activation_target_budget_usd(user: dict, order: dict, plan: Any) -> float:
    total = Decimal("0")
    for paid_order in db.get_orders_for_email(user["email"]):
        if paid_order.get("status") != "paid":
            continue
        paid_spec = _order_activation_spec(paid_order)
        if paid_spec:
            total += Decimal(str(paid_spec.max_budget_usd))
    if order.get("status") != "paid":
        total += Decimal(str(plan.max_budget_usd))

    current_budget = Decimal("0")
    try:
        key_info = get_key_info(user["litellm_key"])
        current_budget = Decimal(str(key_info.get("max_budget") or "0"))
    except Exception as exc:
        db.log_event("payment", f"budget read skipped: {_redact_error(exc)}", order["out_trade_no"])

    target = max(total, current_budget).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return float(target)


def _activate_paid_order(order: dict, raw_notify: str, trade_no: str | None) -> str:
    if order["status"] == "paid":
        return order.get("email_status") or "already-paid"

    user = db.get_user(order["user_id"])
    plan = _order_activation_spec(order)
    if not user or not plan:
        raise RuntimeError("订单关联的用户或激活规格不存在")

    target_budget = _activation_target_budget_usd(user, order, plan)
    activate_key(user["litellm_key"], user["email"], plan, order["out_trade_no"], max_budget_usd=target_budget)
    db.mark_user_key_active(user["id"])
    email_status = send_payment_success_email(
        to_email=user["email"],
        api_key=user["litellm_key"],
        plan=plan,
        out_trade_no=order["out_trade_no"],
    )
    db.mark_order_paid(order["out_trade_no"], trade_no, raw_notify, email_status)
    db.log_event(
        "payment",
        f"paid and activated plan={plan.id} target_budget_usd={target_budget} email_status={email_status}",
        order["out_trade_no"],
    )
    return email_status


def _send_registration_notice(user: dict) -> str:
    try:
        email_status = send_registration_email(
            to_email=user["email"],
            api_key=user["litellm_key"],
        )
    except Exception as exc:
        email_status = f"failed: {exc}"
    db.log_event("mail", f"registration email {email_status}", user["email"])
    return email_status


def _sync_catalog_from_litellm() -> dict[str, Any]:
    catalog = _load_catalog_or_500()
    team = catalog.setdefault("team", {})
    team_alias = team.get("alias") or "ai-proxy"
    teams = get_teams()
    target_team = None
    for item in teams:
        if item.get("team_alias") == team_alias or item.get("team_id") == team.get("team_id"):
            target_team = item
            break
    if target_team:
        team["team_id"] = target_team.get("team_id") or team.get("team_id")
        team["alias"] = target_team.get("team_alias") or team_alias
    team_models = set(target_team.get("models") or []) if target_team else set()
    restrict_to_team = bool(team.get("restrict_to_team_models", True))

    existing = {model["id"]: model for model in catalog.get("models", []) if model.get("id")}
    for row in get_model_info():
        model_id = row.get("model_name")
        if not model_id:
            continue
        info = row.get("model_info") or {}
        current = existing.get(model_id, {"id": model_id, "name": model_id, "description": "", "tags": []})
        current.update(
            {
                "max_tokens": info.get("max_tokens"),
                "max_input_tokens": info.get("max_input_tokens"),
                "max_output_tokens": info.get("max_output_tokens"),
                "input_cost_per_token": info.get("input_cost_per_token"),
                "output_cost_per_token": info.get("output_cost_per_token"),
            }
        )
        if restrict_to_team:
            current["enabled"] = model_id in team_models
        else:
            current["enabled"] = bool(current.get("enabled", True))
        existing[model_id] = current

    catalog["models"] = sorted(existing.values(), key=lambda item: (not item.get("enabled", True), item["id"]))
    return save_catalog(catalog)


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    db.purge_expired_auth_rows()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    return (STATIC_DIR / "admin.html").read_text(encoding="utf-8")


@app.get("/docs")
def docs_redirect() -> RedirectResponse:
    return RedirectResponse("docs/")


@app.get("/docs/", response_class=HTMLResponse)
def docs_listing_page(sort: str = "updated") -> str:
    return _docs_listing_html("views" if sort == "views" else "updated")


@app.get("/docs/api", response_class=HTMLResponse)
def api_docs_page() -> str:
    return _static_doc_page("docs.html", "docs:api")


@app.get("/docs/codex", response_class=HTMLResponse)
def codex_docs_page() -> str:
    return _static_doc_page("codex-docs.html", "docs:codex")


@app.get("/docs/claude-code", response_class=HTMLResponse)
def claude_code_docs_page() -> str:
    return _static_doc_page("claude-code-docs.html", "docs:claude-code")


@app.get("/docs/agent-client", response_class=HTMLResponse)
def agent_client_docs_page() -> str:
    return _static_doc_page("agent-client-docs.html", "docs:agent-client")


@app.get("/agent/sessions/{session_pk}", response_class=HTMLResponse)
def agent_session_page(session_pk: int) -> str:
    html = (STATIC_DIR / "agent-session.html").read_text(encoding="utf-8")
    return html.replace("__AGENT_SESSION_ID__", str(session_pk))


@app.get("/news")
def news_redirect() -> RedirectResponse:
    return RedirectResponse("news/")


@app.get("/news/", response_class=HTMLResponse)
def news_listing_page(sort: str = "updated") -> str:
    return _news_listing_html("views" if sort == "views" else "updated")


@app.get("/news/{news_id}", response_class=HTMLResponse)
def news_detail_page(news_id: str) -> str:
    item = _get_news_item(news_id)
    views = db.increment_page_view(f"news:{news_id}")
    return _news_detail_html(item, views)


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(token: str) -> str:
    reset = db.get_password_reset(token)
    html = (STATIC_DIR / "reset.html").read_text(encoding="utf-8")
    return html.replace("__RESET_TOKEN__", token if reset else "")


@app.get("/health")
def health() -> dict:
    catalog = public_catalog()
    return {
        "ok": True,
        "zpay_configured": zpay_is_configured(),
        "plans": [plan["id"] for plan in catalog["plans"]],
        "team": catalog["team"].get("alias") or catalog["team"].get("team_id"),
    }


@app.get("/api/news")
def list_news(sort: str = "updated") -> dict:
    return {"news": _sort_by_date_and_views([_public_news_item(item) for item in NEWS_ITEMS], sort)}


@app.get("/api/docs/views")
def list_doc_views(sort: str = "updated") -> dict:
    return {"docs": _sort_by_date_and_views([_public_doc_item(item) for item in DOC_ITEMS], sort)}


@app.get("/api/news/{news_id}")
def get_news(news_id: str) -> dict:
    item = _get_news_item(news_id)
    return {
        "news": {
            **_public_news_item(item),
            "body": item.get("body", []),
            "changes": item.get("changes", []),
            "images": item.get("images", []),
        }
    }


@app.post("/api/agent/events")
async def record_agent_event(request: Request) -> dict:
    user = _agent_event_user(request)
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Agent 上报内容必须是 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Agent 上报内容必须是 JSON object")
    normalized = _normalize_agent_payload(payload, request)
    redacted_payload = _redact_payload(payload)
    session = db.upsert_agent_event(
        user["id"],
        client=normalized["client"],
        session_id=normalized["session_id"],
        event_type=normalized["event_type"],
        payload_json=json.dumps(redacted_payload, ensure_ascii=False),
        first_prompt=normalized["first_prompt"],
        status=normalized["status"],
        current_tool=normalized["current_tool"],
        current_step=normalized["current_step"],
        model=normalized["model"],
        context_window=normalized["context_window"],
        used_tokens=normalized["used_tokens"],
        remaining_context=normalized["remaining_context"],
        cwd=normalized["cwd"],
        finished=normalized["finished"],
    )
    return {"ok": True, "session": _public_agent_session(session)}


@app.get("/api/agent/sessions")
def list_agent_sessions(request: Request) -> dict:
    user = _current_user(request, extend_session=False)
    sessions = [_public_agent_session(row) for row in db.list_agent_sessions(user["id"], limit=50)]
    active_statuses = {"running", "tool_running", "tool_done", "compacting", "subagent_running"}
    return {
        "sessions": sessions,
        "summary": {
            "total": len(sessions),
            "active": sum(1 for item in sessions if item["status"] in active_statuses),
            "stale": sum(1 for item in sessions if item["status"] == "stale"),
            "completed": sum(1 for item in sessions if item["status"] == "completed"),
        },
    }


@app.get("/api/agent/sessions/{session_pk}")
def get_agent_session(request: Request, session_pk: int) -> dict:
    user = _current_user(request, extend_session=False)
    session = db.get_agent_session(user["id"], session_pk)
    if not session:
        raise HTTPException(status_code=404, detail="Agent 会话不存在")
    events = db.list_agent_events(user["id"], session_pk, limit=300)
    return {
        "session": _public_agent_session(session),
        "events": [_public_agent_event(row) for row in events],
    }


@app.get("/api/plans")
def list_plans() -> dict:
    catalog = public_catalog()
    return {
        "plans": catalog["plans"],
        "catalog": catalog,
        "pay_types": [{"id": key, "name": value} for key, value in PAY_TYPES.items()],
        "site_name": settings.site_name,
        "litellm_base_url": settings.litellm_public_base_url,
        "zpay_configured": zpay_is_configured(),
    }


@app.get("/api/resource-status")
def resource_status_snapshot(request: Request) -> dict:
    _current_user(request, extend_session=False)
    if not settings.resource_status_url:
        raise HTTPException(status_code=503, detail="资源状态接口未配置")
    try:
        response = requests.get(settings.resource_status_url, timeout=(3, 10))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"资源状态读取失败: {_redact_error(exc)}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"资源状态读取失败: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="资源状态接口返回格式不正确") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="资源状态接口返回格式不正确")
    return _public_resource_status(payload)


@app.get("/api/admin/catalog")
def admin_get_catalog(request: Request) -> dict:
    _require_admin(request)
    return {"catalog": load_catalog()}


@app.put("/api/admin/catalog")
def admin_put_catalog(request: Request, payload: AdminCatalogRequest) -> dict:
    _require_admin(request)
    try:
        catalog = save_catalog(payload.catalog)
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.log_event("admin", "catalog updated")
    return {"catalog": catalog}


@app.post("/api/admin/catalog/sync")
def admin_sync_catalog(request: Request) -> dict:
    _require_admin(request)
    try:
        catalog = _sync_catalog_from_litellm()
    except (CatalogError, LiteLLMError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.log_event("admin", "catalog synced from litellm")
    return {"catalog": catalog}


@app.post("/api/auth/register")
@app.post("/api/register")
def register(response: Response, payload: RegisterRequest) -> dict:
    email = _clean_email(payload.email)
    password = _clean_password(payload.password)
    existing = db.get_user_by_email(email)

    if existing and existing.get("password_hash"):
        raise HTTPException(status_code=409, detail="邮箱已注册，请直接登录")

    if existing:
        user = db.set_user_password(existing["id"], hash_password(password))
        email_status = _send_registration_notice(user)
        db.log_event("register", "attached password to existing user", email)
        created = False
    else:
        try:
            litellm_key = generate_pending_key(email, team_id=team_config(_load_catalog_or_500()).get("team_id"))
        except LiteLLMError as exc:
            db.log_event("register", f"litellm key generation failed: {exc}", email)
            raise HTTPException(status_code=502, detail="API Key 创建失败，请稍后再试") from exc
        user = db.create_user(email, litellm_key)
        user = db.set_user_password(user["id"], hash_password(password))
        email_status = _send_registration_notice(user)
        db.log_event("register", "created pending user, password and key", email)
        created = True

    _login_user(response, user)
    return {
        **_dashboard(user, include_usage=False),
        "created": created,
        "registration_email_status": email_status,
    }


@app.post("/api/auth/login")
def login(response: Response, payload: LoginRequest) -> dict:
    email = _clean_email(payload.email)
    user = db.get_user_by_email(email)
    if not user or not verify_password(payload.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    _login_user(response, user)
    refreshed = db.get_user(user["id"]) or user
    return _dashboard(refreshed, include_usage=False)


@app.post("/api/auth/key-login")
def key_login(response: Response, payload: ApiKeyLoginRequest) -> dict:
    api_key = _clean_api_key(payload.api_key)
    user = _user_from_api_key(api_key, import_external=True)
    if not user:
        raise HTTPException(status_code=401, detail="API Key 不正确")
    _login_user(response, user)
    refreshed = db.get_user(user["id"]) or user
    return _dashboard(refreshed, include_usage=False)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    _clear_session(request, response)
    return {"ok": True}


@app.post("/api/auth/forgot")
def forgot_password(payload: ForgotPasswordRequest) -> dict:
    email = _clean_email(payload.email)
    user = db.get_user_by_email(email)
    if user:
        token = new_token()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_MINUTES)
        db.create_password_reset(token, user["id"], expires_at.isoformat())
        reset_url = f"{settings.portal_base_url}/reset/{token}"
        try:
            email_status = send_password_reset_email(to_email=email, reset_url=reset_url)
        except Exception as exc:
            email_status = f"failed: {exc}"
        db.log_event("mail", f"password reset email {email_status}", email)
    return {"ok": True}


@app.post("/api/auth/reset")
def reset_password(response: Response, payload: ResetPasswordRequest) -> dict:
    password = _clean_password(payload.password)
    user = db.consume_password_reset(payload.token, hash_password(password))
    if not user:
        raise HTTPException(status_code=400, detail="重置链接无效或已过期")
    _login_user(response, user)
    db.log_event("auth", "password reset completed", user["email"])
    return _dashboard(user, include_usage=False)


@app.get("/api/dashboard")
def full_dashboard(request: Request) -> dict:
    user = _current_user(request)
    return _dashboard(user, include_usage=False)


@app.get("/api/session")
def session_snapshot(request: Request) -> dict:
    user = _current_user(request)
    return _dashboard(user, include_usage=False)


@app.get("/api/usage")
def usage_snapshot(request: Request) -> dict:
    user = _current_user(request, extend_session=request.query_params.get("activity") == "1")
    return {
        "user": _public_user(user),
        "usage": _cached_usage(user, force=request.query_params.get("activity") == "1"),
    }


def _bounded_float(value: float | None, *, default: float | None, minimum: float, maximum: float) -> float | None:
    if value is None:
        return default
    parsed = float(value)
    return max(minimum, min(parsed, maximum))


def _optional_bounded_float(
    value: float | None,
    *,
    minimum: float,
    maximum: float,
    omit_if: float | None = None,
) -> float | None:
    parsed = _bounded_float(value, default=None, minimum=minimum, maximum=maximum)
    if parsed is None:
        return None
    if omit_if is not None and parsed == omit_if:
        return None
    return parsed


def _prepare_chat_request(user: dict, payload: ChatCompletionRequest) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    model = payload.model.strip()
    key_info = _active_key_info(user)
    allowed_models = _models_from_key_info(key_info)
    if model not in allowed_models:
        raise HTTPException(status_code=403, detail="当前 Key 未订购该模型")
    messages = _chat_messages(payload.messages)
    max_tokens = payload.max_tokens
    if max_tokens is not None and (max_tokens <= 0 or max_tokens > 8192):
        raise HTTPException(status_code=422, detail="max_tokens 必须在 1 - 8192 之间")
    params = {
        "max_tokens": max_tokens,
        "temperature": _optional_bounded_float(payload.temperature, minimum=0.0, maximum=2.0),
        "top_p": _optional_bounded_float(payload.top_p, minimum=0.01, maximum=1.0, omit_if=1.0),
        "presence_penalty": _optional_bounded_float(payload.presence_penalty, minimum=-2.0, maximum=2.0, omit_if=0.0),
        "frequency_penalty": _optional_bounded_float(payload.frequency_penalty, minimum=-2.0, maximum=2.0, omit_if=0.0),
    }
    return model, messages, params


def _chat_metadata(user: dict) -> dict[str, Any]:
    return {
        "source": "ai-proxy-portal-chat",
        "portal_user_id": user["id"],
        "portal_user_email": user["email"],
        "portal_key": _mask_api_key(user["litellm_key"]),
    }


@app.post("/api/chat/completions")
def chat(request: Request, payload: ChatCompletionRequest) -> dict:
    user = _current_user(request, extend_session=True)
    model, messages, params = _prepare_chat_request(user, payload)
    try:
        data = chat_completion(
            user["litellm_key"],
            model=model,
            messages=messages,
            **params,
            user_id=user["email"],
            metadata=_chat_metadata(user),
        )
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"模型调用失败: {_redact_error(exc)}") from exc

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    return {
        "model": data.get("model") or model,
        "message": {
            "role": message.get("role") or "assistant",
            "content": message.get("content") or "",
        },
        "usage": usage,
        "id": data.get("id"),
    }


@app.get("/api/chat/threads")
def list_chat_threads(request: Request) -> dict:
    user = _current_user(request, extend_session=False)
    return {"threads": [_public_chat_thread(row) for row in db.list_chat_threads(user["id"])]}


@app.put("/api/chat/threads/{thread_id}")
def save_chat_thread(request: Request, thread_id: str, payload: ChatThreadSaveRequest) -> dict:
    user = _current_user(request, extend_session=False)
    clean_id = re.sub(r"[^A-Za-z0-9_.:-]", "", thread_id)[:80]
    if not clean_id:
        raise HTTPException(status_code=422, detail="对话 ID 不正确")
    messages = _clean_saved_chat_messages(payload.messages)
    if not messages:
        raise HTTPException(status_code=422, detail="对话内容不能为空")
    title = payload.title.strip()[:80] or "新对话"
    model = payload.model.strip()[:120]
    last_usage = payload.last_usage if isinstance(payload.last_usage, dict) else None
    row = db.upsert_chat_thread(
        user["id"],
        thread_id=clean_id,
        title=title,
        model=model,
        messages_json=json.dumps(messages, ensure_ascii=False),
        last_usage_json=json.dumps(last_usage, ensure_ascii=False) if last_usage else None,
    )
    return {"thread": _public_chat_thread(row)}


@app.delete("/api/chat/threads")
def clear_chat_threads(request: Request) -> dict:
    user = _current_user(request, extend_session=False)
    db.delete_chat_threads(user["id"])
    return {"ok": True}


@app.post("/api/chat/stream")
def chat_stream(request: Request, payload: ChatCompletionRequest) -> StreamingResponse:
    user = _current_user(request, extend_session=True)
    model, messages, params = _prepare_chat_request(user, payload)
    try:
        upstream = chat_completion_stream(
            user["litellm_key"],
            model=model,
            messages=messages,
            **params,
            user_id=user["email"],
            metadata=_chat_metadata(user),
        )
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"模型调用失败: {_redact_error(exc)}") from exc

    def stream_events():
        request_id = None
        usage: dict[str, Any] = {}
        try:
            for raw_line in upstream.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_text)
                except json.JSONDecodeError:
                    continue
                request_id = chunk.get("id") or request_id
                if chunk.get("usage"):
                    usage = chunk.get("usage") or {}
                    yield _sse("usage", {"usage": usage})
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    yield _sse("delta", {"content": content})
            yield _sse("done", {"usage": usage, "id": request_id})
        except Exception as exc:
            yield _sse("error", {"detail": _redact_error(exc)})
        finally:
            upstream.close()

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.api_route("/anthropic/{anthropic_path:path}", methods=["POST"])
def anthropic_proxy(request: Request, anthropic_path: str, payload: dict[str, Any]) -> Response:
    if anthropic_path not in {"v1/messages", "v1/messages/count_tokens"}:
        return _budget_error(404, "Anthropic 代理路径不存在")
    api_key = _api_token_from_request(request)
    if not api_key:
        return _budget_error(401, "缺少 API Key")
    try:
        _require_active_api_key(api_key, payload)
    except HTTPException as exc:
        return _budget_error(exc.status_code, str(exc.detail))

    upstream_url = f"{settings.litellm_base_url}/{anthropic_path}"
    try:
        upstream = requests.post(
            upstream_url,
            headers=_anthropic_forward_headers(request, api_key),
            json=payload,
            stream=bool(payload.get("stream")),
            timeout=(15, 600),
        )
    except requests.RequestException as exc:
        return _budget_error(502, f"模型调用失败: {_redact_error(exc)}")

    media_type = upstream.headers.get("content-type") or "application/json"
    if upstream.status_code >= 400:
        content = upstream.content
        upstream.close()
        return Response(content=content, status_code=upstream.status_code, media_type=media_type)

    if not payload.get("stream"):
        content = upstream.content
        upstream.close()
        return Response(content=content, status_code=upstream.status_code, media_type=media_type)

    def stream_upstream():
        try:
            for chunk in upstream.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        stream_upstream(),
        status_code=upstream.status_code,
        media_type=media_type,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/auth/touch")
def touch_session(request: Request) -> dict:
    _current_user(request, extend_session=True)
    return {"ok": True}


@app.post("/api/key/copy")
def copy_api_key(request: Request) -> dict:
    user = _current_user(request, extend_session=True)
    return {
        "api_key": user["litellm_key"],
        "api_key_masked": _mask_api_key(user["litellm_key"]),
    }


@app.post("/api/orders")
def create_order(request: Request, payload: CreateOrderRequest) -> dict:
    if not zpay_is_configured():
        raise HTTPException(status_code=503, detail="支付服务未配置 ZPAY_PID/ZPAY_KEY")

    user = _current_user(request)
    if payload.email and _clean_email(payload.email) != user["email"]:
        raise HTTPException(status_code=403, detail="不能为其它账号创建订单")

    try:
        if payload.plan_id == "custom":
            plan = custom_spec(payload.custom or {}, _load_catalog_or_500())
        else:
            plan = get_plan(payload.plan_id, _load_catalog_or_500())
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not plan:
        raise HTTPException(status_code=404, detail="套餐不存在或已下架")
    if payload.pay_type not in PAY_TYPES:
        raise HTTPException(status_code=422, detail="支付方式不支持")

    out_trade_no = _new_order_no()
    notify_url = f"{settings.portal_base_url}/api/pay/notify"
    return_url = f"{settings.portal_base_url}/return/{out_trade_no}"
    payment_url = build_payment_url(
        amount=plan.price_cny,
        product_name=f"{settings.site_name} - {plan.name}",
        out_trade_no=out_trade_no,
        pay_type=payload.pay_type,
        notify_url=notify_url,
        return_url=return_url,
    )
    order = db.create_order(
        out_trade_no=out_trade_no,
        user_id=user["id"],
        plan_id=plan.id,
        order_type=plan.order_type,
        amount_cny=f"{plan.price_cny:.2f}",
        pay_type=payload.pay_type,
        payment_url=payment_url,
        plan_snapshot=json.dumps(plan.public_dict(), ensure_ascii=False, sort_keys=True),
        activation_spec=json.dumps(plan.activation_dict(), ensure_ascii=False, sort_keys=True),
    )
    db.log_event("order", f"created plan={plan.id} pay_type={payload.pay_type}", out_trade_no)
    return {"order": _public_order(order)}


@app.get("/api/orders/{out_trade_no}")
def order_status(request: Request, out_trade_no: str) -> dict:
    user = _current_user(request)
    order = db.get_order(out_trade_no)
    if not order or order["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"order": _public_order(order)}


@app.get("/api/orders/{out_trade_no}/refund")
def refund_quote(request: Request, out_trade_no: str) -> dict:
    user = _current_user(request)
    order = db.get_order(out_trade_no)
    if not order or order["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="订单不存在")
    try:
        quote = _refund_quote(order, user)
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"用量读取失败，暂不能计算退款: {_redact_error(exc)}") from exc
    return {"order": _public_order(order), "quote": quote}


@app.post("/api/orders/{out_trade_no}/refund")
def confirm_refund(request: Request, out_trade_no: str) -> dict:
    user = _current_user(request)
    order = db.get_order(out_trade_no)
    if not order or order["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.get("status") == "refund_pending":
        quote = _refund_quote(order, user)
        return {"order": _public_order(order), "quote": quote, "user": _public_user(user)}
    try:
        quote = _refund_quote(order, user)
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"用量读取失败，暂不能提交退款: {_redact_error(exc)}") from exc

    try:
        budget_adjustment = _deduct_refund_order_budget(user, order, quote)
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"额度扣减失败，暂不能提交退款: {_redact_error(exc)}") from exc
    quote["budget_deduction"] = budget_adjustment

    db.mark_order_refund_pending(
        out_trade_no,
        channel_fee_cny=quote["channel_fee_cny"],
        token_cost_cny=quote["token_cost_cny"],
        token_cost_usd=quote["token_cost_usd"],
        refund_amount_cny=quote["refund_amount_cny"],
        quote_json=json.dumps(quote, ensure_ascii=False, sort_keys=True),
    )
    db.log_event(
        "refund",
        (
            f"manual refund requested amount={quote['refund_amount_cny']} "
            f"channel_fee={quote['channel_fee_cny']} token_cost={quote['token_cost_cny']} "
            f"deducted_budget_usd={budget_adjustment['deducted_budget_usd']} "
            f"new_max_budget_usd={budget_adjustment['new_max_budget_usd']}"
        ),
        out_trade_no,
    )
    updated_order = db.get_order(out_trade_no) or order
    updated_user = db.get_user(user["id"]) or user
    return {"order": _public_order(updated_order), "quote": quote, "user": _public_user(updated_user)}


@app.post("/api/orders/{out_trade_no}/refund/cancel")
def cancel_refund(request: Request, out_trade_no: str) -> dict:
    user = _current_user(request)
    order = db.get_order(out_trade_no)
    if not order or order["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.get("status") != "refund_pending":
        raise HTTPException(status_code=409, detail="当前订单没有退款申请")
    try:
        budget_restore = _restore_refund_order_budget(user, order)
    except LiteLLMError as exc:
        raise HTTPException(status_code=502, detail=f"额度恢复失败，暂不能取消退款: {_redact_error(exc)}") from exc
    db.cancel_order_refund(out_trade_no)
    db.log_event(
        "refund",
        (
            f"refund canceled restored_max_budget_usd={budget_restore['restored_max_budget_usd']} "
            f"previous_max_budget_usd={budget_restore['previous_max_budget_usd']}"
        ),
        out_trade_no,
    )
    updated_order = db.get_order(out_trade_no) or order
    updated_user = db.get_user(user["id"]) or user
    return {"order": _public_order(updated_order), "budget_restore": budget_restore, "user": _public_user(updated_user)}


@app.get("/api/users/{email}/orders")
def user_orders(request: Request, email: str) -> dict:
    user = _current_user(request)
    clean = _clean_email(email)
    if clean != user["email"]:
        raise HTTPException(status_code=403, detail="不能查看其它账号")
    orders = db.get_orders_for_email(clean)
    return {"user": _public_user(user), "orders": [_public_order(order) for order in orders]}


@app.api_route("/api/pay/notify", methods=["GET", "POST"])
async def pay_notify(request: Request) -> PlainTextResponse:
    if request.method == "POST":
        body = (await request.body()).decode("utf-8", errors="replace")
        params = {key: value for key, value in parse_qsl(body, keep_blank_values=True)}
    else:
        params = {key: value for key, value in request.query_params.items()}

    raw_notify = json.dumps(params, ensure_ascii=False, sort_keys=True)
    out_trade_no = params.get("out_trade_no", "")
    if not out_trade_no:
        db.log_event("payment", f"missing out_trade_no raw={raw_notify}")
        return PlainTextResponse("fail")

    order = db.get_order(out_trade_no)
    if not order:
        db.log_event("payment", f"unknown order raw={raw_notify}", out_trade_no)
        return PlainTextResponse("fail")

    if not verify_zpay(params):
        db.log_event("payment", f"bad sign raw={raw_notify}", out_trade_no)
        return PlainTextResponse("fail")

    paid = params.get("trade_status") == "TRADE_SUCCESS" or params.get("status") in {"1", "paid", "success"}
    received_amount = params.get("money")
    if received_amount and Decimal(str(received_amount)) != Decimal(order["amount_cny"]):
        db.log_event("payment", f"amount mismatch raw={raw_notify}", out_trade_no)
        return PlainTextResponse("fail")

    if order.get("status") in {"refund_pending", "refunded"}:
        db.log_event("payment", f"ignored notify for status={order['status']} raw={raw_notify}", out_trade_no)
        return PlainTextResponse("success")

    if not paid:
        db.mark_order_failed(out_trade_no, raw_notify)
        db.log_event("payment", f"notify not paid raw={raw_notify}", out_trade_no)
        return PlainTextResponse("success")

    try:
        _activate_paid_order(order, raw_notify, params.get("trade_no"))
    except Exception as exc:
        db.log_event("payment", f"activation failed: {exc}", out_trade_no)
        return PlainTextResponse("fail")
    return PlainTextResponse("success")


@app.get("/return/{out_trade_no}", response_class=HTMLResponse)
def pay_return(out_trade_no: str) -> str:
    order = db.get_order(out_trade_no)
    if not order:
        return RedirectResponse(url=f"{settings.portal_base_url}/")
    html = (STATIC_DIR / "return.html").read_text(encoding="utf-8")
    return html.replace("__ORDER_NO__", out_trade_no)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("portal.main:app", host=settings.bind_host, port=settings.port, reload=False)
