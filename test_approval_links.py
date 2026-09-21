from __future__ import annotations

import hashlib
import hmac
import json
import os

import approval_links


def main() -> None:
    os.environ["PM_EDGE_APPROVAL_SECRET"] = "test-secret-do-not-use"
    token = approval_links.create_approval_token(
        ticker="KXMLBGAME-26SEP171235MILPIT-MIL",
        side="YES",
        lane="primary",
        execution="TAKE",
        limit_price=0.38,
        shares=9,
        max_loss=3.42,
        fair_probability=0.558,
        net_edge=0.149,
        books=6,
        now=1_000_000,
        ttl_seconds=7200,
    )
    decoded = approval_links.verify_approval_token(token, now=1_000_001)
    assert decoded["ticker"] == "KXMLBGAME-26SEP171235MILPIT-MIL"
    assert decoded["side"] == "YES"
    assert decoded["lane"] == "primary"
    assert decoded["execution"] == "TAKE"
    assert decoded["limit_price"] == 0.38
    assert decoded["shares"] == 9
    assert decoded["max_loss"] == 3.42
    assert decoded["fair_probability"] == 0.558
    assert decoded["net_edge"] == 0.149
    assert decoded["books"] == 6

    # A still-valid alert created before signal_id provenance must continue to
    # open safely instead of crashing the approval page.
    legacy_payload = {
        "v": approval_links.TOKEN_VERSION,
        "t": "LEGACY-FISH",
        "s": "Y",
        "l": "f",
        "a": "M",
        "p": 4200,
        "n": 5,
        "r": 210,
        "x": 1_007_200,
    }
    legacy_body = approval_links._b64e(
        json.dumps(legacy_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    legacy_sig = hmac.new(
        approval_links.signing_secret(),
        legacy_body.encode("ascii"),
        hashlib.sha256,
    ).digest()[:16]
    legacy_token = f"{legacy_body}.{approval_links._b64e(legacy_sig)}"
    legacy = approval_links.verify_approval_token(legacy_token, now=1_000_001)
    assert legacy["ticker"] == "LEGACY-FISH"
    assert legacy["lane"] == "price_fish"
    assert legacy["legacy_signal_id"] is True
    assert len(legacy["signal_id"]) == 20
    assert approval_links.verify_approval_token(
        legacy_token, now=1_000_002
    )["signal_id"] == legacy["signal_id"]

    limit_token = approval_links.create_approval_token(
        ticker="LIMIT-TEST",
        side="NO",
        lane="price_fish",
        execution="MAKE",
        limit_price=0.42,
        shares=5,
        max_loss=2.10,
        now=1_000_000,
    )
    assert approval_links.verify_approval_token(limit_token, now=1_000_001)["execution"] == "MAKE"

    url = approval_links.build_approval_url("https://example.streamlit.app/", token)
    assert url.startswith("https://example.streamlit.app/Approve?trade=")
    assert len(url) < 512

    body, signature = token.split(".", 1)
    tampered_body = ("A" if body[0] != "A" else "B") + body[1:]
    tampered = tampered_body + "." + signature
    try:
        approval_links.verify_approval_token(tampered, now=1_000_001)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered token should fail")

    try:
        approval_links.verify_approval_token(token, now=1_007_201)
    except ValueError as exc:
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("expired token should fail")

    print("Approval link tests: PASS")


if __name__ == "__main__":
    main()
