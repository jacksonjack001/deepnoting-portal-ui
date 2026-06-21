from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import urllib.request
from typing import Any
from urllib.parse import urlparse

from .settings import settings


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _tencent_ses_host() -> str:
    parsed = urlparse(settings.tencent_ses_endpoint)
    return parsed.netloc or parsed.path


def _tc3_headers(action: str, payload: str) -> dict[str, str]:
    service = "ses"
    host = _tencent_ses_host()
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(dt.datetime.now(dt.timezone.utc).timestamp())
    date = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime("%Y-%m-%d")

    canonical_uri = "/"
    canonical_querystring = ""
    content_type = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join(
        [
            "POST",
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            _sha256_hex(payload),
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    secret_date = _hmac_sha256(("TC3" + settings.tencent_secret_key).encode("utf-8"), date)
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={settings.tencent_secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": "2020-10-02",
        "X-TC-Region": settings.tencent_region,
    }


def _send_tencent_email(
    to_email: str,
    subject: str,
    template_data: dict[str, str],
    template_id: str | None = None,
) -> str:
    payload = {
        "FromEmailAddress": settings.tencent_from,
        "ReplyToAddresses": settings.tencent_reply_to,
        "Destination": [to_email],
        "Subject": subject,
        "Template": {
            "TemplateID": int(template_id or settings.tencent_template_id),
            "TemplateData": json.dumps(template_data, ensure_ascii=False),
        },
        "TriggerType": 1,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    req = urllib.request.Request(
        settings.tencent_ses_endpoint,
        data=body.encode("utf-8"),
        headers=_tc3_headers("SendEmail", body),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if "Error" in data.get("Response", {}):
        raise RuntimeError(json.dumps(data["Response"]["Error"], ensure_ascii=False))
    return data["Response"].get("MessageId", "")


def _ses_configured() -> bool:
    return bool(
        settings.tencent_secret_id
        and settings.tencent_secret_key
        and settings.tencent_template_id
        and settings.tencent_ses_endpoint
    )


def _base_template_data(*, to_email: str, api_key: str) -> dict[str, str]:
    return {
        "email": to_email,
        "api_key": api_key,
        "base_url": settings.litellm_public_base_url,
        "docs_url": f"{settings.portal_base_url}/docs",
    }


def send_registration_email(*, to_email: str, api_key: str) -> str:
    if not _ses_configured():
        return "skipped: tencent ses env vars are not configured"

    message_id = _send_tencent_email(
        to_email,
        "欢迎注册AI Proxy",
        _base_template_data(to_email=to_email, api_key=api_key),
    )
    return f"sent:{message_id}"


def send_password_reset_email(*, to_email: str, reset_url: str) -> str:
    if not _ses_configured():
        return "skipped: tencent ses env vars are not configured"

    message_id = _send_tencent_email(
        to_email,
        "AI Proxy密码重置",
        {
            "email": to_email,
            "reset_url": reset_url,
            "api_key": f"密码重置链接：{reset_url}",
            "base_url": settings.litellm_public_base_url,
            "docs_url": f"{settings.portal_base_url}/docs",
        },
        settings.tencent_reset_template_id,
    )
    return f"sent:{message_id}"


def send_payment_success_email(
    *,
    to_email: str,
    api_key: str,
    plan: Any,
    out_trade_no: str,
) -> str:
    if not _ses_configured():
        return "skipped: tencent ses env vars are not configured"

    template_data = {
        "plan": plan.name,
        "models": ", ".join(plan.models),
        "amount": f"{plan.price_cny:.2f}",
        "order_no": out_trade_no,
        **_base_template_data(to_email=to_email, api_key=api_key),
    }
    message_id = _send_tencent_email(to_email, "AI Proxy Portal交易完成", template_data)
    return f"sent:{message_id}"
