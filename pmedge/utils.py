from __future__ import annotations
from datetime import datetime, timezone
import math
import re
import unicodedata


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def ceil_to(v: float, step: float) -> float:
    if step <= 0:
        raise ValueError("step must be positive")
    return round(math.ceil((v - 1e-12) / step) * step, 6)


def floor_to(v: float, step: float) -> float:
    if step <= 0:
        raise ValueError("step must be positive")
    return round(math.floor((v + 1e-12) / step) * step, 6)


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\bno\.?\b", "north", s)
    s = re.sub(r"\bst\.?\b", "state", s)
    s = re.sub(r"\buniversity\b", "", s)
    s = re.sub(r"\bcollege\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
