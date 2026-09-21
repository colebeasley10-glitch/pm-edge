from __future__ import annotations
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MarketSpec:
    ticker: str
    event_name: str
    participant_a: str | None
    participant_b: str | None
    market_type: str
    selection: str | None
    point: float | None
    parse_confidence: float
    reason: str


def _participants(event_name: str):
    # Common sports title separators: "A vs B", "A at B", "A @ B", "A v B".
    patterns = [
        r"(.+?)\s+(?:vs\.?|v\.?)\s+(.+)",
        r"(.+?)\s+at\s+(.+)",
        r"(.+?)\s+@\s+(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, event_name, flags=re.I)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None


def parse_market(m: dict) -> MarketSpec:
    ticker = str(m.get("ticker", ""))
    title = str(m.get("title") or "").strip()
    subtitle = str(m.get("subtitle") or "").strip()
    yes_sub = str(m.get("yes_sub_title") or "").strip()
    rules = " ".join([str(m.get("rules_primary") or ""), str(m.get("rules_secondary") or "")])
    blob = " | ".join([title, subtitle, yes_sub, rules])

    event_name = title.split(":", 1)[0].strip() if ":" in title else title
    a, b = _participants(event_name)

    # Team total before generic total.
    mt = re.search(r"([A-Za-z0-9 .&()'/-]+?)\s+over\s+(-?\d+(?:\.\d+)?)\s+(?:points?|runs?|goals?)\s+scored", blob, flags=re.I)
    if "team total" in title.lower() or mt:
        if mt:
            return MarketSpec(ticker, event_name, a, b, "team_total", mt.group(1).strip(), float(mt.group(2)), 0.96, "team total text")
        return MarketSpec(ticker, event_name, a, b, "team_total", None, None, 0.50, "team total title but outcome not parsed")

    ms = re.search(r"([A-Za-z0-9 .&()'/-]+?)\s+wins?\s+by\s+over\s+(-?\d+(?:\.\d+)?)\s+(?:points?|runs?|goals?)", blob, flags=re.I)
    if "spread" in title.lower() or ms:
        if ms:
            # Sportsbook convention: favorite to win by over X = team spread -X.
            return MarketSpec(ticker, event_name, a, b, "spread", ms.group(1).strip(), -float(ms.group(2)), 0.98, "spread text")
        return MarketSpec(ticker, event_name, a, b, "spread", None, None, 0.50, "spread title but outcome not parsed")

    mtot = re.search(r"over\s+(-?\d+(?:\.\d+)?)\s+(?:points?|runs?|goals?)\s+scored", blob, flags=re.I)
    if "total points" in title.lower() or mtot:
        if mtot:
            return MarketSpec(ticker, event_name, a, b, "total", "Over", float(mtot.group(1)), 0.98, "total text")
        return MarketSpec(ticker, event_name, a, b, "total", "Over", None, 0.50, "total title but point not parsed")

    mw = re.search(r"will\s+(.+?)\s+win\??", blob, flags=re.I)
    if mw:
        return MarketSpec(ticker, event_name, a, b, "h2h", mw.group(1).strip(), None, 0.94, "win question")

    if a and b:
        if yes_sub and yes_sub.lower() not in {"yes", "no"}:
            return MarketSpec(ticker, event_name, a, b, "h2h", yes_sub, None, 0.88, "yes subtitle")
        return MarketSpec(ticker, event_name, a, b, "h2h", None, None, 0.55, "game recognized; selection unresolved")

    return MarketSpec(ticker, event_name, a, b, "unsupported", None, None, 0.0, "not a supported sports market")
