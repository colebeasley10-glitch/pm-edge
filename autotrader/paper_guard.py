"""Hard fail-closed guards for AutoTrader paper mode."""
from __future__ import annotations

import importlib


class PaperOrderClient:
    """A deliberate non-client: every order mutation raises."""

    @classmethod
    def from_env(cls, *args, **kwargs):
        return cls()

    def _blocked(self, *args, **kwargs):
        raise RuntimeError("paper mode forbids Kalshi order submission")

    create_buy_order = _blocked
    create_sell_order = _blocked
    submit_order = _blocked
    place_order = _blocked
    cancel_order = _blocked
    amend_order = _blocked


def install_paper_order_stub() -> None:
    """Replace the repository's authenticated submit client in paper mode."""
    try:
        module = importlib.import_module("kalshi_submit")
    except Exception:
        return
    module.KalshiSubmitClient = PaperOrderClient
