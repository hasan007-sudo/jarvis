"""Local Ebbinghaus memory-strength math (port of memory-scoring.ts + memory-kind.ts)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

DEFAULT_DECAY_LAMBDA = 0.03
KIND_DECAY: dict[str, float] = {
    "procedural": 0.006, "semantic": 0.018, "episodic": 0.07,
    "sop": 0.006, "workflow": 0.01, "fact": 0.018,
    "preference": 0.02, "gotcha": 0.03, "episode": 0.07,
}
MIN_STRENGTH = 0.05
EXPIRED_MULTIPLIER = 0.35

_MEMORY_KEYS = (
    "event_time", "ingestion_time", "last_accessed", "valid_to", "decay_lambda",
    "access_count", "access_ema", "importance", "confidence", "memory_kind",
    "memory_layer", "valid_from", "supersedes",
)


def _now_ms() -> float:
    return datetime.now(timezone.utc).timestamp() * 1000


def _ts_ms(value) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _num_or(value, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else fallback


def decay_lambda(meta: dict) -> float:
    explicit = meta.get("decay_lambda")
    if isinstance(explicit, (int, float)) and math.isfinite(explicit):
        return float(explicit)
    return KIND_DECAY.get(meta.get("memory_kind", "fact"), DEFAULT_DECAY_LAMBDA)


def _has_memory_metadata(meta: dict) -> bool:
    return any(meta.get(k) is not None for k in _MEMORY_KEYS)


def _is_expired(valid_to, now: float) -> bool:
    exp = _ts_ms(valid_to)
    return exp is not None and exp < now


def memory_strength(meta: dict | None, now: float | None = None) -> float:
    if now is None:
        now = _now_ms()
    if not meta or meta.get("keep") is True:
        return 1.0
    if meta.get("memory_layer") == "archive":
        return 0.0
    if not _has_memory_metadata(meta):
        return 1.0
    last_seen = _ts_ms(meta.get("last_accessed") or meta.get("ingestion_time")) or now
    age_days = max(0.0, (now - last_seen) / 86_400_000)
    lam = decay_lambda(meta)
    access = _num_or(meta.get("access_ema"), _num_or(meta.get("access_count"), 0))
    quality = max(0.1, _num_or(meta.get("importance"), 1) * _num_or(meta.get("confidence"), 1))
    reinforcement = 1 + math.log1p(access) / 4
    expiry = EXPIRED_MULTIPLIER if _is_expired(meta.get("valid_to"), now) else 1
    raw = quality * reinforcement * math.exp(-lam * age_days) * expiry
    return min(1.0, max(MIN_STRENGTH, raw))


def is_prompt_safe(meta: dict | None) -> bool:
    if not meta:
        return True
    return (
        meta.get("privacy_class") != "sensitive"
        and meta.get("memory_layer") != "malicious"
        and meta.get("tombstone") is not True
    )
