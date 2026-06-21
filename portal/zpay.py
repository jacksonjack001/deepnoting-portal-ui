from __future__ import annotations

import hashlib
from decimal import Decimal
from urllib.parse import urlencode

from .settings import settings


PAY_TYPES = {
    "alipay": "支付宝",
    "wxpay": "微信支付",
    "qqpay": "QQ 钱包",
}


def is_configured() -> bool:
    return bool(settings.zpay_pid and settings.zpay_key)


def _sign_string(params: dict[str, str]) -> str:
    filtered = {
        key: value
        for key, value in params.items()
        if value != "" and key not in {"sign", "sign_type"}
    }
    return "&".join(f"{key}={filtered[key]}" for key in sorted(filtered))


def sign(params: dict[str, str]) -> str:
    raw = _sign_string(params) + settings.zpay_key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify(params: dict[str, str]) -> bool:
    received = params.get("sign", "")
    if not received or not settings.zpay_key:
        return False
    return received.lower() == sign(params).lower()


def build_payment_url(
    *,
    amount: Decimal,
    product_name: str,
    out_trade_no: str,
    pay_type: str,
    notify_url: str,
    return_url: str,
) -> str:
    if not is_configured():
        raise RuntimeError("ZPAY_PID/ZPAY_KEY is not configured")
    if pay_type not in PAY_TYPES:
        raise ValueError("unsupported pay type")

    params = {
        "money": f"{amount:.2f}",
        "name": product_name,
        "notify_url": notify_url,
        "out_trade_no": out_trade_no,
        "pid": settings.zpay_pid,
        "return_url": return_url,
        "sitename": settings.site_name,
        "type": pay_type,
    }
    params["sign"] = sign(params)
    params["sign_type"] = "MD5"
    return f"{settings.zpay_gateway}submit.php?{urlencode(params)}"
