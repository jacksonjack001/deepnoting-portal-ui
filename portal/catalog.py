from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .settings import ROOT


CATALOG_PATH = Path(os.getenv("PORTAL_CATALOG_PATH", str(ROOT / "config" / "portal_catalog.json")))


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ActivationSpec:
    id: str
    name: str
    description: str
    price_cny: Decimal
    models: list[str]
    max_budget_usd: float
    duration: str
    rpm_limit: int
    tpm_limit: int
    total_request_limit: int | None = None
    five_hour_request_limit: int | None = None
    daily_request_limit: int | None = None
    weekly_request_limit: int | None = None
    monthly_request_limit: int | None = None
    request_tier: str | None = None
    rpm_tier: str | None = None
    tpm_tier: str | None = None
    custom_equivalent_price_cny: Decimal | None = None
    discount_cny: Decimal | None = None
    discount_rate: Decimal | None = None
    capacity_policy: dict[str, Any] | None = None
    order_type: str = "plan"
    team_id: str | None = None

    def to_dict(self, *, include_internal: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "price_cny": f"{self.price_cny:.2f}",
            "models": self.models,
            "max_budget_usd": self.max_budget_usd,
            "duration": self.duration,
            "rpm_limit": self.rpm_limit,
            "tpm_limit": self.tpm_limit,
            "total_request_limit": self.total_request_limit,
            "five_hour_request_limit": self.five_hour_request_limit,
            "daily_request_limit": self.daily_request_limit,
            "weekly_request_limit": self.weekly_request_limit,
            "monthly_request_limit": self.monthly_request_limit,
            "request_tier": self.request_tier,
            "rpm_tier": self.rpm_tier,
            "tpm_tier": self.tpm_tier,
            "custom_equivalent_price_cny": f"{self.custom_equivalent_price_cny:.2f}"
            if self.custom_equivalent_price_cny is not None
            else None,
            "discount_cny": f"{self.discount_cny:.2f}" if self.discount_cny is not None else None,
            "discount_rate": float(self.discount_rate) if self.discount_rate is not None else None,
            "order_type": self.order_type,
        }
        if include_internal:
            data["team_id"] = self.team_id
            data["capacity_policy"] = self.capacity_policy or {}
        return data

    def public_dict(self) -> dict[str, Any]:
        return self.to_dict(include_internal=False)

    def activation_dict(self) -> dict[str, Any]:
        return self.to_dict(include_internal=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActivationSpec":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            price_cny=Decimal(str(data["price_cny"])).quantize(Decimal("0.01")),
            models=[str(model) for model in data.get("models", [])],
            max_budget_usd=float(data["max_budget_usd"]),
            duration=str(data.get("duration", "30d")),
            rpm_limit=int(data.get("rpm_limit", 60)),
            tpm_limit=int(data.get("tpm_limit", 60000)),
            total_request_limit=_optional_int(data.get("total_request_limit")),
            five_hour_request_limit=_optional_int(data.get("five_hour_request_limit")),
            daily_request_limit=_optional_int(data.get("daily_request_limit")),
            weekly_request_limit=_optional_int(data.get("weekly_request_limit")),
            monthly_request_limit=_optional_int(data.get("monthly_request_limit")),
            request_tier=data.get("request_tier"),
            rpm_tier=data.get("rpm_tier"),
            tpm_tier=data.get("tpm_tier"),
            custom_equivalent_price_cny=_optional_money(data.get("custom_equivalent_price_cny")),
            discount_cny=_optional_money(data.get("discount_cny")),
            discount_rate=_optional_decimal(data.get("discount_rate")),
            capacity_policy=dict(data.get("capacity_policy") or {}),
            order_type=str(data.get("order_type", "plan")),
            team_id=data.get("team_id"),
        )


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _optional_money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return _money(value)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _budget(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _optional_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    parsed = int(value)
    return parsed if parsed > 0 else default


def _positive_int(value: Any, field: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise CatalogError(f"{field} 必须大于 0")
    return parsed


def _validate_option_ids(options: list[dict[str, Any]], field: str) -> set[str]:
    seen: set[str] = set()
    for option in options:
        option_id = str(option.get("id", "")).strip()
        if not option_id:
            raise CatalogError(f"{field}.id 不能为空")
        if option_id in seen:
            raise CatalogError(f"重复 {field}.id: {option_id}")
        seen.add(option_id)
    return seen


def _default_capacity_policy() -> dict[str, Any]:
    return {
        "customer_share_ratio": 0.5,
        "note": "内部容量策略，仅后台使用。",
        "providers": [],
    }


def capacity_policy(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    data = catalog or load_catalog()
    policy = dict(data.get("capacity_policy") or _default_capacity_policy())
    policy.setdefault("customer_share_ratio", 0.5)
    policy.setdefault("note", "内部容量策略，仅后台使用。")
    policy.setdefault("providers", [])
    return policy


def load_catalog() -> dict[str, Any]:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"配置文件不存在: {CATALOG_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"配置 JSON 解析失败: {exc}") from exc
    validate_catalog(data)
    return data


def save_catalog(data: dict[str, Any]) -> dict[str, Any]:
    validate_catalog(data)
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CATALOG_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(CATALOG_PATH)
    return data


def validate_catalog(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise CatalogError("配置必须是 JSON object")
    team = data.get("team") or {}
    policy = data.get("capacity_policy") or {}
    ratio = Decimal(str(policy.get("customer_share_ratio", 0.5)))
    if ratio <= 0 or ratio > Decimal("0.5"):
        raise CatalogError("capacity_policy.customer_share_ratio 必须在 0 - 0.5 之间")
    for provider in policy.get("providers", []):
        provider_ratio = Decimal(str(provider.get("customer_share_ratio", ratio)))
        if provider_ratio <= 0 or provider_ratio > Decimal("0.5"):
            raise CatalogError("provider.customer_share_ratio 必须在 0 - 0.5 之间")

    models = data.get("models")
    plans = data.get("plans")
    if not isinstance(models, list) or not models:
        raise CatalogError("models 必须是非空数组")
    if not isinstance(plans, list) or not plans:
        raise CatalogError("plans 必须是非空数组")

    model_ids: set[str] = set()
    enabled_model_ids: set[str] = set()
    for model in models:
        model_id = str(model.get("id", "")).strip()
        if not model_id:
            raise CatalogError("model.id 不能为空")
        if model_id in model_ids:
            raise CatalogError(f"重复 model.id: {model_id}")
        model_ids.add(model_id)
        if model.get("enabled", True):
            enabled_model_ids.add(model_id)

    plan_ids: set[str] = set()
    for plan in plans:
        plan_id = str(plan.get("id", "")).strip()
        if not plan_id:
            raise CatalogError("plan.id 不能为空")
        if plan_id in plan_ids:
            raise CatalogError(f"重复 plan.id: {plan_id}")
        plan_ids.add(plan_id)
        _money(plan.get("price_cny", "0"))
        if float(plan.get("max_budget_usd", 0)) < 0:
            raise CatalogError(f"{plan_id} max_budget_usd 不能小于 0")
        _positive_int(plan.get("rpm_limit", 60), f"{plan_id}.rpm_limit")
        _positive_int(plan.get("tpm_limit", 60000), f"{plan_id}.tpm_limit")
        for field in (
            "total_request_limit",
            "five_hour_request_limit",
            "daily_request_limit",
            "weekly_request_limit",
            "monthly_request_limit",
        ):
            if plan.get(field) not in (None, "") and int(plan[field]) <= 0:
                raise CatalogError(f"{plan_id}.{field} 必须大于 0")
        for model_id in plan.get("models", []):
            if model_id not in model_ids:
                raise CatalogError(f"{plan_id} 引用了不存在的模型: {model_id}")
            if plan.get("enabled", True) and model_id not in enabled_model_ids:
                raise CatalogError(f"{plan_id} 引用了未上架模型: {model_id}")

    custom = data.get("custom") or {}
    if custom.get("enabled", False):
        min_budget = _budget(custom.get("min_budget_usd", 0))
        max_budget = _budget(custom.get("max_budget_usd", 0))
        if min_budget <= 0 or max_budget < min_budget:
            raise CatalogError("custom 预算范围配置不正确")
        if int(custom.get("min_models", 1)) <= 0:
            raise CatalogError("custom.min_models 必须大于 0")
        if int(custom.get("max_models", 1)) < int(custom.get("min_models", 1)):
            raise CatalogError("custom.max_models 必须大于等于 min_models")
        request_ids = _validate_option_ids(custom.get("request_options") or [], "custom.request_options")
        rpm_ids = _validate_option_ids(custom.get("rpm_options") or [], "custom.rpm_options")
        tpm_ids = _validate_option_ids(custom.get("tpm_options") or [], "custom.tpm_options")
        for option in custom.get("request_options") or []:
            for field in (
                "total_request_limit",
                "five_hour_request_limit",
                "daily_request_limit",
                "weekly_request_limit",
                "monthly_request_limit",
            ):
                if option.get(field) not in (None, "") and int(option[field]) <= 0:
                    raise CatalogError(f"custom.request_options.{option['id']}.{field} 必须大于 0")
            _money(option.get("price_cny", "0"))
        for option in custom.get("rpm_options") or []:
            _positive_int(option.get("rpm_limit"), f"custom.rpm_options.{option['id']}.rpm_limit")
            _money(option.get("price_cny", "0"))
        for option in custom.get("tpm_options") or []:
            _positive_int(option.get("tpm_limit"), f"custom.tpm_options.{option['id']}.tpm_limit")
            _money(option.get("price_cny", "0"))
        for key, valid_ids in (
            ("default_request_option", request_ids),
            ("default_rpm_option", rpm_ids),
            ("default_tpm_option", tpm_ids),
        ):
            if custom.get(key) and custom[key] not in valid_ids:
                raise CatalogError(f"custom.{key} 不存在")


def team_config(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    return (catalog or load_catalog()).get("team") or {}


def enabled_models(catalog: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = catalog or load_catalog()
    return [model for model in data.get("models", []) if model.get("enabled", True)]


def model_map(catalog: dict[str, Any] | None = None, *, enabled_only: bool = False) -> dict[str, dict[str, Any]]:
    data = catalog or load_catalog()
    rows = enabled_models(data) if enabled_only else data.get("models", [])
    return {str(model["id"]): model for model in rows}


def public_catalog() -> dict[str, Any]:
    data = load_catalog()
    public_custom = dict(data.get("custom") or {})
    public_team = {
        "alias": (data.get("team") or {}).get("alias"),
        "restrict_to_team_models": bool((data.get("team") or {}).get("restrict_to_team_models", True)),
    }
    return {
        "version": data.get("version", 1),
        "team": public_team,
        "models": enabled_models(data),
        "plans": [plan.public_dict() for plan in list_plans(data)],
        "custom": public_custom,
    }


def list_plans(catalog: dict[str, Any] | None = None) -> list[ActivationSpec]:
    data = catalog or load_catalog()
    team_id = team_config(data).get("team_id")
    plans: list[ActivationSpec] = []
    for raw in data.get("plans", []):
        if raw.get("enabled", True):
            priced = _priced_plan_payload(raw, data)
            payload = {
                **priced,
                "capacity_policy": capacity_policy(data),
                "order_type": "plan",
                "team_id": team_id,
            }
            plans.append(ActivationSpec.from_dict(payload))
    plans.sort(key=lambda item: int(next((p.get("sort_order", 100) for p in data["plans"] if p["id"] == item.id), 100)))
    return plans


def get_plan(plan_id: str, catalog: dict[str, Any] | None = None) -> ActivationSpec | None:
    for plan in list_plans(catalog):
        if plan.id == plan_id:
            return plan
    return None


def _options_with_fallback(custom: dict[str, Any], key: str, fallback: dict[str, Any]) -> list[dict[str, Any]]:
    options = custom.get(key)
    if isinstance(options, list) and options:
        return [dict(option) for option in options]
    return [fallback]


def _select_option(
    options: list[dict[str, Any]],
    selected_id: str | None,
    default_id: str | None,
    label: str,
) -> dict[str, Any]:
    option_id = selected_id or default_id or options[0].get("id")
    for option in options:
        if option.get("id") == option_id:
            return option
    raise CatalogError(f"{label} 选项不支持")


def _model_output_limit(model: dict[str, Any]) -> int:
    limits: list[int] = []
    for field in ("max_output_tokens", "max_tokens"):
        value = model.get(field)
        if value in (None, ""):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            limits.append(parsed)
    return max(limits, default=0)


def _minimum_tpm_option(
    tpm_options: list[dict[str, Any]],
    required_tpm: int,
) -> dict[str, Any] | None:
    if required_tpm <= 0:
        return None
    candidates = []
    for option in tpm_options:
        try:
            tpm_limit = int(option.get("tpm_limit") or 0)
        except (TypeError, ValueError):
            continue
        if tpm_limit >= required_tpm:
            candidates.append((tpm_limit, option))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _discount_rate_for_plan(plan: dict[str, Any], catalog: dict[str, Any]) -> Decimal:
    custom = catalog.get("custom") or {}
    value = plan.get("discount_rate", custom.get("preset_discount_rate", "0.10"))
    rate = Decimal(str(value))
    if rate < 0 or rate >= 1:
        raise CatalogError(f"{plan.get('id', 'plan')}.discount_rate 必须在 0 - 1 之间")
    return rate


def _priced_plan_payload(plan: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    payload = dict(plan)
    if not payload.get("discount_from_custom", True):
        return payload
    custom = catalog.get("custom") or {}
    if not custom.get("enabled", False):
        return payload
    equivalent = custom_spec(
        {
            "model_ids": payload.get("models", []),
            "budget_usd": payload.get("max_budget_usd", 0),
            "duration": payload.get("duration") or custom.get("default_duration", "30d"),
            "request_option": payload.get("request_tier") or custom.get("default_request_option"),
            "rpm_option": payload.get("rpm_tier") or custom.get("default_rpm_option"),
            "tpm_option": payload.get("tpm_tier") or custom.get("default_tpm_option"),
            "auto_tpm": False,
        },
        catalog,
    )
    discount_rate = _discount_rate_for_plan(payload, catalog)
    price_cny = _money(equivalent.price_cny * (Decimal("1") - discount_rate))
    payload["price_cny"] = f"{price_cny:.2f}"
    payload["custom_equivalent_price_cny"] = f"{equivalent.price_cny:.2f}"
    payload["discount_cny"] = f"{_money(equivalent.price_cny - price_cny):.2f}"
    payload["discount_rate"] = str(discount_rate)
    return payload


def custom_spec(payload: dict[str, Any], catalog: dict[str, Any] | None = None) -> ActivationSpec:
    data = catalog or load_catalog()
    custom = data.get("custom") or {}
    if not custom.get("enabled", False):
        raise CatalogError("自定义组合当前不可用")

    models_by_id = model_map(data, enabled_only=True)
    requested_models = [str(model).strip() for model in payload.get("model_ids", []) if str(model).strip()]
    requested_models = list(dict.fromkeys(requested_models))
    min_models = int(custom.get("min_models", 1))
    max_models = int(custom.get("max_models", 6))
    if len(requested_models) < min_models:
        raise CatalogError(f"至少选择 {min_models} 个模型")
    if len(requested_models) > max_models:
        raise CatalogError(f"最多选择 {max_models} 个模型")
    missing = [model for model in requested_models if model not in models_by_id]
    if missing:
        raise CatalogError(f"模型不可售或不存在: {', '.join(missing)}")

    min_budget = _budget(custom.get("min_budget_usd", 0.05))
    max_budget = _budget(custom.get("max_budget_usd", 50))
    budget_usd = _budget(payload.get("budget_usd", custom.get("default_budget_usd", min_budget)))
    if budget_usd < min_budget or budget_usd > max_budget:
        raise CatalogError(f"预算必须在 {min_budget} - {max_budget} USD 之间")

    duration = str(payload.get("duration") or custom.get("default_duration", "30d"))
    duration_options = [str(item) for item in custom.get("duration_options", [])]
    if duration_options and duration not in duration_options:
        raise CatalogError("有效期选项不支持")

    cny_per_usd = Decimal(str(custom.get("cny_per_usd", 7.3)))
    multiplier = Decimal(str(custom.get("price_multiplier", 1)))
    min_price = _money(custom.get("min_price_cny", "0.10"))
    request_options = _options_with_fallback(
        custom,
        "request_options",
        {
            "id": "standard",
            "name": "标准请求",
            "total_request_limit": custom.get("total_request_limit"),
            "five_hour_request_limit": custom.get("five_hour_request_limit"),
            "daily_request_limit": custom.get("daily_request_limit"),
            "weekly_request_limit": custom.get("weekly_request_limit"),
            "monthly_request_limit": custom.get("monthly_request_limit"),
            "price_cny": "0.00",
        },
    )
    rpm_options = _options_with_fallback(
        custom,
        "rpm_options",
        {
            "id": "standard",
            "name": f"{custom.get('rpm_limit', 60)} RPM",
            "rpm_limit": custom.get("rpm_limit", 60),
            "price_cny": "0.00",
        },
    )
    tpm_options = _options_with_fallback(
        custom,
        "tpm_options",
        {
            "id": "standard",
            "name": f"{custom.get('tpm_limit', 60000)} TPM",
            "tpm_limit": custom.get("tpm_limit", 60000),
            "price_cny": "0.00",
        },
    )
    request_option = _select_option(
        request_options,
        payload.get("request_option") or payload.get("request_option_id"),
        custom.get("default_request_option"),
        "请求数",
    )
    rpm_option = _select_option(
        rpm_options,
        payload.get("rpm_option") or payload.get("rpm_option_id"),
        custom.get("default_rpm_option"),
        "RPM",
    )
    tpm_option = _select_option(
        tpm_options,
        payload.get("tpm_option") or payload.get("tpm_option_id"),
        custom.get("default_tpm_option"),
        "TPM",
    )
    if payload.get("auto_tpm", True):
        required_tpm = max(_model_output_limit(models_by_id[model]) for model in requested_models)
        selected_tpm = _positive_int(tpm_option.get("tpm_limit", custom.get("tpm_limit", 60000)), "tpm_limit")
        if required_tpm > selected_tpm:
            upgraded_tpm_option = _minimum_tpm_option(tpm_options, required_tpm)
            if not upgraded_tpm_option:
                raise CatalogError(f"所选模型至少需要 {required_tpm} TPM，当前没有可覆盖的 TPM 档位")
            tpm_option = upgraded_tpm_option

    budget_price = _money(budget_usd * cny_per_usd * multiplier)
    option_price = _money(request_option.get("price_cny", "0")) + _money(rpm_option.get("price_cny", "0")) + _money(
        tpm_option.get("price_cny", "0")
    )
    price_cny = max(_money(budget_price + option_price), min_price)
    model_names = [models_by_id[model].get("name") or model for model in requested_models]
    total_requests = _optional_int(request_option.get("total_request_limit"))
    five_hour_requests = _optional_int(request_option.get("five_hour_request_limit"))
    daily_requests = _optional_int(request_option.get("daily_request_limit"))
    weekly_requests = _optional_int(request_option.get("weekly_request_limit"))
    monthly_requests = _optional_int(request_option.get("monthly_request_limit"))
    rpm_limit = _positive_int(rpm_option.get("rpm_limit", custom.get("rpm_limit", 60)), "rpm_limit")
    tpm_limit = _positive_int(tpm_option.get("tpm_limit", custom.get("tpm_limit", 60000)), "tpm_limit")
    return ActivationSpec(
        id="custom",
        name=str(custom.get("name") or "自定义组合"),
        description=f"{', '.join(model_names)} / 预算 ${budget_usd} / {total_requests or '不限'} 次请求 / {rpm_limit} RPM / {tpm_limit} TPM",
        price_cny=price_cny,
        models=requested_models,
        max_budget_usd=float(budget_usd),
        duration=duration,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        total_request_limit=total_requests,
        five_hour_request_limit=five_hour_requests,
        daily_request_limit=daily_requests,
        weekly_request_limit=weekly_requests,
        monthly_request_limit=monthly_requests,
        request_tier=str(request_option.get("id")),
        rpm_tier=str(rpm_option.get("id")),
        tpm_tier=str(tpm_option.get("id")),
        capacity_policy=capacity_policy(data),
        order_type="custom",
        team_id=team_config(data).get("team_id"),
    )


def activation_from_snapshot(snapshot: str | None) -> ActivationSpec | None:
    if not snapshot:
        return None
    try:
        data = json.loads(snapshot)
    except json.JSONDecodeError:
        return None
    return ActivationSpec.from_dict(data)
