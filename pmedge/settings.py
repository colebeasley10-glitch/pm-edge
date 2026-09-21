from dataclasses import dataclass
import os

def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw in (None, "") else float(raw)

def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)

def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _s(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return default if raw is None else raw.strip()

@dataclass(frozen=True)
class Settings:
    odds_api_key: str = _s("ODDS_API_KEY")
    odds_regions: str = _s("ODDS_REGIONS", "us")
    odds_sports: tuple[str, ...] = tuple(
        x.strip() for x in _s("ODDS_SPORTS", "AUTO").split(",") if x.strip()
    )
    odds_fetch_alternates: bool = _b("ODDS_FETCH_ALTERNATES", True)
    odds_min_books: int = _i("ODDS_MIN_BOOKS", 3)
    odds_max_dispersion: float = _f("ODDS_MAX_DISPERSION", 0.10)
    odds_skip_outright_only: bool = _b("ODDS_SKIP_OUTRIGHT_ONLY", False)

    kalshi_base_url: str = _s("KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2")
    kalshi_api_key_id: str = _s("KALSHI_API_KEY_ID")
    kalshi_private_key_path: str = _s("KALSHI_PRIVATE_KEY_PATH")

    mode: str = _s("PM_EDGE_MODE", "paper").lower()
    live_arm_token: str = _s("PM_EDGE_LIVE_ARM_TOKEN")

    min_net_edge: float = _f("PM_EDGE_MIN_NET_EDGE", 0.04)
    model_error_buffer: float = _f("PM_EDGE_MODEL_ERROR_BUFFER", 0.01)
    slippage_buffer: float = _f("PM_EDGE_SLIPPAGE_BUFFER", 0.0025)
    min_match_confidence: float = _f("PM_EDGE_MIN_MATCH_CONFIDENCE", 0.82)
    min_volume_24h: float = _f("PM_EDGE_MIN_VOLUME_24H", 0.0)
    max_markets: int = _i("PM_EDGE_MAX_MARKETS", 3000)

    taker_fee_rate: float = _f("PM_EDGE_TAKER_FEE_RATE", 0.07)
    maker_fee_rate: float = _f("PM_EDGE_MAKER_FEE_RATE", 0.0175)

    bankroll_dollars: float = _f("PM_EDGE_BANKROLL_DOLLARS", 100.0)
    kelly_fraction: float = _f("PM_EDGE_KELLY_FRACTION", 0.25)
    max_trade_pct: float = _f("PM_EDGE_MAX_TRADE_PCT", 0.02)
    max_daily_pct: float = _f("PM_EDGE_MAX_DAILY_PCT", 0.08)
    max_event_pct: float = _f("PM_EDGE_MAX_EVENT_PCT", 0.04)
    max_contracts: int = _i("PM_EDGE_MAX_CONTRACTS", 100)
    min_contracts: int = _i("PM_EDGE_MIN_CONTRACTS", 1)

    db_path: str = _s("PM_EDGE_DB_PATH", "data/pmedge.sqlite3")

    @property
    def nonfee_buffer(self) -> float:
        return self.model_error_buffer + self.slippage_buffer

    @property
    def live_enabled(self) -> bool:
        return self.mode == "live" and self.live_arm_token == "ARM_LIVE_TRADING"
