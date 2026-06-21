from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_SERVICES_PATH = Path(
    os.getenv("PORTAL_EXTERNAL_SERVICES_PATH", str(ROOT / "config" / "external_services.json"))
)


def _load_external_services() -> dict:
    try:
        return json.loads(EXTERNAL_SERVICES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"外部服务配置 JSON 解析失败: {EXTERNAL_SERVICES_PATH}: {exc}") from exc


_external_services = _load_external_services()


def _configured_url(key: str, env_key: str, default: str = "") -> str:
    return os.getenv(env_key, str(_external_services.get(key) or default)).rstrip("/")


@dataclass(frozen=True)
class Settings:
    portal_base_url: str = _configured_url("portal_base_url", "PORTAL_BASE_URL")
    bind_host: str = os.getenv("PORTAL_BIND_HOST", "127.0.0.1")
    port: int = int(os.getenv("PORTAL_PORT", "8090"))
    db_path: Path = Path(os.getenv("PORTAL_DB_PATH", str(ROOT / "portal" / "data" / "portal.db")))
    site_name: str = os.getenv("PORTAL_SITE_NAME", "AI Proxy Portal")

    litellm_base_url: str = _configured_url("litellm_base_url", "LITELLM_BASE_URL")
    litellm_public_base_url: str = _configured_url("litellm_public_base_url", "LITELLM_PUBLIC_BASE_URL")
    litellm_master_key: str = os.getenv("LITELLM_MASTER_KEY", "")
    portal_admin_token: str = os.getenv("PORTAL_ADMIN_TOKEN", "")
    resource_status_url: str = _configured_url("resource_status_url", "RESOURCE_STATUS_URL")

    zpay_gateway: str = _configured_url("zpay_gateway", "ZPAY_GATEWAY").rstrip("/") + "/"
    zpay_pid: str = os.getenv("ZPAY_PID", "")
    zpay_key: str = os.getenv("ZPAY_KEY", "")
    refund_channel_fee_rate: str = os.getenv("PORTAL_REFUND_CHANNEL_FEE_RATE", "0.006")
    refund_channel_fee_fixed_cny: str = os.getenv("PORTAL_REFUND_CHANNEL_FEE_FIXED_CNY", "0")
    refund_channel_fee_min_cny: str = os.getenv("PORTAL_REFUND_CHANNEL_FEE_MIN_CNY", "0.01")

    tencent_secret_id: str = os.getenv("TENCENTCLOUD_SECRET_ID", "")
    tencent_secret_key: str = os.getenv("TENCENTCLOUD_SECRET_KEY", "")
    tencent_ses_endpoint: str = _configured_url("tencent_ses_endpoint", "TENCENT_SES_ENDPOINT")
    tencent_region: str = os.getenv("TENCENT_SES_REGION", "ap-guangzhou")
    tencent_from: str = os.getenv("TENCENT_SES_FROM", "AI Proxy <no-reply@example.com>")
    tencent_reply_to: str = os.getenv("TENCENT_SES_REPLY_TO", "support@example.com")
    tencent_template_id: str = os.getenv("TENCENT_SES_TEMPLATE_ID", "")
    tencent_reset_template_id: str = os.getenv(
        "TENCENT_SES_RESET_TEMPLATE_ID",
        os.getenv("TENCENT_SES_TEMPLATE_ID", ""),
    )


settings = Settings()
