"""Scalar validation and immutable JSON values; this module has no Qt dependency."""

import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from uuid import UUID, uuid4


class ModelError(ValueError):
    """Invalid input at a model boundary, with a stable field path."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


def new_id() -> str:
    return str(uuid4())


def uuid_value(value: str, field: str) -> str:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        raise ModelError(field, "必须是规范形式的 UUID v4 字符串")
    return value


def uuid_sequence(values, field: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ModelError(field, "必须是 ID 序列")
    return tuple(uuid_value(value, f"{field}[{i}]") for i, value in enumerate(values))


def text_value(value: str, field: str, *, maximum: int | None = None, required=False) -> str:
    if not isinstance(value, str):
        raise ModelError(field, "必须是文本")
    if required and not value.strip():
        raise ModelError(field, "不得为空白")
    if maximum is not None and len(value) > maximum:
        raise ModelError(field, f"最多允许 {maximum} 个字符")
    return value


def number_value(value, field: str, *, minimum=None, maximum=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError(field, "必须是有限数值")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ModelError(field, "数值超出范围") from exc
    if not math.isfinite(result):
        raise ModelError(field, "不得为 NaN 或 Infinity")
    if minimum is not None and result < minimum:
        raise ModelError(field, f"不得小于 {minimum}")
    if maximum is not None and result > maximum:
        raise ModelError(field, f"不得大于 {maximum}")
    return result


def integer_value(value, field: str, *, minimum=0) -> int:
    if type(value) is not int or value < minimum:
        raise ModelError(field, f"必须是大于等于 {minimum} 的整数")
    return value


def boolean_value(value, field: str) -> bool:
    if type(value) is not bool:
        raise ModelError(field, "必须是布尔值")
    return value


def timestamp_value(value, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ModelError(field, "必须是带时区的时间")
    return value


def freeze_json(value, field: str = "extensions"):
    """Copy recursively so mutable caller values cannot change a domain snapshot."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return number_value(value, field)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ModelError(field, "对象的键必须是字符串")
        return MappingProxyType(
            {key: freeze_json(item, f"{field}.{key}") for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item, f"{field}[{i}]") for i, item in enumerate(value))
    raise ModelError(field, "不支持的 JSON 值类型")


def json_object(value, field: str):
    if not isinstance(value, Mapping):
        raise ModelError(field, "必须是对象")
    return freeze_json(value, field)


def object_map(values, expected_type, field: str):
    if not isinstance(values, Mapping):
        raise ModelError(field, "必须是 ID 到对象的映射")
    result = {}
    for key, item in values.items():
        uuid_value(key, f"{field}.key")
        if not isinstance(item, expected_type):
            raise ModelError(f"{field}.{key}", f"必须是 {expected_type.__name__}")
        result[key] = item
    return MappingProxyType(result)
