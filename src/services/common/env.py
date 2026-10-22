"""Typed readers for RT_* environment variables."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class SettingsError(ValueError):
    """Invalid service configuration. main() exits with code 2."""


def env_str(env: Mapping[str, str], key: str, default: Optional[str]) -> Optional[str]:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _check_range(
    key: str,
    value: float,
    min_value: Optional[float],
    max_value: Optional[float],
) -> None:
    if min_value is not None and value < min_value:
        raise SettingsError(f"{key} must be >= {min_value}, got {value}")
    if max_value is not None and value > max_value:
        raise SettingsError(f"{key} must be <= {max_value}, got {value}")


def env_int(
    env: Mapping[str, str],
    key: str,
    default: int,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> int:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    _check_range(key, value, min_value, max_value)
    return value


def env_float(
    env: Mapping[str, str],
    key: str,
    default: float,
    *,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> float:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number, got {raw!r}") from exc
    _check_range(key, value, min_value, max_value)
    return value


def env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    low = raw.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise SettingsError(f"{key} must be a boolean (true/false), got {raw!r}")


def env_choice(
    env: Mapping[str, str],
    key: str,
    default: str,
    choices: Sequence[str],
) -> str:
    raw = env_str(env, key, None)
    if raw is None:
        return default
    low = raw.lower()
    if low not in choices:
        raise SettingsError(f"{key} must be one of {', '.join(choices)}, got {raw!r}")
    return low
