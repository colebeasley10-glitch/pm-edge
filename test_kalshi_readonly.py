from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_readonly import KalshiReadOnlyClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/portfolio/orders"):
            return FakeResponse({"orders": [{"order_id": "o1"}]})
        if url.endswith("/portfolio/fills"):
            return FakeResponse({"fills": [{"trade_id": "f1"}]})
        raise AssertionError(url)


def main() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    session = FakeSession()
    client = KalshiReadOnlyClient("key-id", pem, session=session)

    timestamp = 1_700_000_000_000
    headers = client._headers("/portfolio/orders?limit=5", timestamp_ms=timestamp)
    assert headers["KALSHI-ACCESS-KEY"] == "key-id"
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = f"{timestamp}GET/trade-api/v2/portfolio/orders".encode("utf-8")
    private.public_key().verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )

    assert client.get_orders(ticker="ABC") == [{"order_id": "o1"}]
    assert client.get_fills(ticker="ABC") == [{"trade_id": "f1"}]
    assert session.calls[0][1]["params"]["ticker"] == "ABC"
    assert session.calls[1][1]["params"]["ticker"] == "ABC"

    for forbidden in ("create_order", "cancel_order", "amend_order", "decrease_order", "post", "delete"):
        assert not hasattr(client, forbidden), forbidden

    print("Kalshi read-only tests: PASS")


if __name__ == "__main__":
    main()
