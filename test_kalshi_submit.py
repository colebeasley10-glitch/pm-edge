from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_submit import KalshiSubmitClient


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.orders = [
            {
                "client_order_id": "pmedge-20260917-old",
                "initial_count_fp": "10.00",
                "outcome_side": "yes",
                "yes_price_dollars": "0.250000",
            }
        ]

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/portfolio/orders"):
            return FakeResponse(200, {"orders": self.orders})
        if method == "GET" and url.endswith("/portfolio/positions"):
            return FakeResponse(200, {"market_positions": [{"ticker": "TEST-YES", "position_fp": "9.00"}]})
        if method == "POST" and url.endswith("/portfolio/events/orders"):
            body = kwargs["json"]
            return FakeResponse(
                201,
                {
                    "order_id": "o-test",
                    "client_order_id": body["client_order_id"],
                    "fill_count": "0.00",
                    "remaining_count": body["count"],
                    "average_fill_price": None,
                    "ts_ms": 1700000000000,
                },
            )
        raise AssertionError((method, url))


def main() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    session = FakeSession()
    client = KalshiSubmitClient("write-key", pem, session=session)

    timestamp = 1_700_000_000_000
    headers = client._headers("POST", "/portfolio/events/orders", timestamp_ms=timestamp)
    assert headers["KALSHI-ACCESS-KEY"] == "write-key"
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = f"{timestamp}POST/trade-api/v2/portfolio/events/orders".encode("utf-8")
    private.public_key().verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )

    yes = client.create_buy_order(
        ticker="TEST-YES",
        outcome_side="YES",
        count=9,
        max_price=0.38,
        execution="TAKE",
        client_order_id="pmedge-yes",
    )
    assert yes["order_id"] == "o-test"
    yes_body = session.calls[-1][2]["json"]
    assert yes_body["side"] == "bid"
    assert yes_body["price"] == "0.3800"
    assert yes_body["count"] == "9.00"
    assert yes_body["time_in_force"] == "immediate_or_cancel"
    assert "post_only" not in yes_body

    no = client.create_buy_order(
        ticker="TEST-NO",
        outcome_side="NO",
        count=6,
        max_price=0.38,
        execution="MAKE",
        client_order_id="pmedge-no",
    )
    assert no["order_id"] == "o-test"
    no_body = session.calls[-1][2]["json"]
    assert no_body["side"] == "ask"
    assert no_body["price"] == "0.6200"
    assert no_body["time_in_force"] == "good_till_canceled"
    assert no_body["post_only"] is True

    round_no = client.create_buy_order(
        ticker="TEST-NO-ROUND",
        outcome_side="NO",
        count=10,
        max_price=0.50,
        execution="TAKE",
        client_order_id="pmedge-no-round",
    )
    assert round_no["order_id"] == "o-test"
    round_no_body = session.calls[-1][2]["json"]
    assert round_no_body["side"] == "ask"
    assert round_no_body["price"] == "0.5000"

    positions = client.get_positions()
    assert positions[0]["ticker"] == "TEST-YES"

    sell_yes = client.create_sell_order(
        ticker="TEST-YES",
        outcome_side="YES",
        count=4,
        min_price=0.57,
        client_order_id="pmedge-sell-yes",
    )
    assert sell_yes["order_id"] == "o-test"
    sell_yes_body = session.calls[-1][2]["json"]
    assert sell_yes_body["side"] == "ask"
    assert sell_yes_body["price"] == "0.5700"
    assert sell_yes_body["time_in_force"] == "immediate_or_cancel"
    assert sell_yes_body["reduce_only"] is True

    sell_no = client.create_sell_order(
        ticker="TEST-NO",
        outcome_side="NO",
        count=4,
        min_price=0.35,
        client_order_id="pmedge-sell-no",
    )
    assert sell_no["order_id"] == "o-test"
    sell_no_body = session.calls[-1][2]["json"]
    assert sell_no_body["side"] == "bid"
    assert sell_no_body["price"] == "0.6500"
    assert sell_no_body["reduce_only"] is True

    assert client.submitted_cost_for_prefix("pmedge-20260917-") == 2.5
    assert client.find_order_by_client_id("pmedge-20260917-old") is not None
    # committed cost: an IOC that missed costs nothing, a partial fill counts only what filled
    session.orders = [
        {"client_order_id": "pmls-1-a", "status": "canceled", "initial_count_fp": "2.00",
         "fill_count_fp": "0.00", "outcome_side": "yes", "yes_price_dollars": "0.500000"},
        {"client_order_id": "pmls-1-b", "status": "canceled", "initial_count_fp": "4.00",
         "fill_count_fp": "1.00", "outcome_side": "no", "no_price_dollars": "0.200000"},
        {"client_order_id": "pmls-1-c", "status": "resting", "initial_count_fp": "1.00",
         "fill_count_fp": "0.00", "outcome_side": "yes", "yes_price_dollars": "0.900000"},
    ]
    assert abs(client.committed_cost_for_prefix("pmls-1-") - (0.20 + 0.02 + 0.90 + 0.01)) < 1e-9

    for forbidden in ("cancel_order", "amend_order", "decrease_order", "transfer", "withdraw"):
        assert not hasattr(client, forbidden), forbidden

    print("Kalshi one-tap submit tests: PASS")



class PagedSession:
    """Serves orders in pages of 2 with a cursor, like Kalshi's 200-per-page listing."""
    def __init__(self, orders, page_size=2):
        self.orders, self.page_size, self.calls = orders, page_size, []

    def request(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append(params)
        start = int(params.get("cursor") or 0)
        page = self.orders[start:start + self.page_size]
        nxt = start + self.page_size
        return FakeResponse(200, {"orders": page, "cursor": str(nxt) if nxt < len(self.orders) else ""})


def _paged_client(session):
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return KalshiSubmitClient("k", pem, session=session)


def test_order_lookups_read_past_the_first_page():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    orders = [{"client_order_id": f"pmls-1-{i}", "created_time": now, "status": "executed",
               "initial_count_fp": "1.00", "fill_count_fp": "1.00", "outcome_side": "yes",
               "yes_price_dollars": "0.500000"} for i in range(5)]
    client = _paged_client(PagedSession(orders))
    assert len(client.get_all_orders()) == 5
    assert client.find_order_by_client_id("pmls-1-4") is not None  # on page 3
    fee = 0.02  # ceil(0.07 * 1 * 0.5 * 0.5 * 100) / 100
    assert abs(client.committed_cost_for_prefix("pmls-1-") - 5 * (0.50 + fee)) < 1e-9


def test_order_paging_stops_at_old_orders_and_refuses_partial_counts():
    import pytest
    from kalshi_submit import KalshiSubmitError
    old = "2020-01-01T00:00:00Z"
    orders = [{"client_order_id": f"x-{i}", "created_time": old} for i in range(10)]
    session = PagedSession(orders)
    client = _paged_client(session)
    assert len(client.get_all_orders()) == 2 and len(session.calls) == 1  # whole page old: stop
    recent = [{"client_order_id": f"y-{i}"} for i in range(10)]  # no timestamps: keep paging
    with pytest.raises(KalshiSubmitError):
        _paged_client(PagedSession(recent)).get_all_orders(max_pages=3)

if __name__ == "__main__":
    main()
