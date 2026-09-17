from __future__ import annotations

import os

import approval_links


def main() -> None:
    os.environ["PM_EDGE_APPROVAL_SECRET"] = "test-secret-do-not-use"
    token = approval_links.create_approval_token(
        ticker="KXMLBGAME-26SEP171235MILPIT-MIL",
        side="YES",
        lane="primary",
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
    assert decoded["limit_price"] == 0.38
    assert decoded["shares"] == 9
    assert decoded["max_loss"] == 3.42
    assert decoded["fair_probability"] == 0.558
    assert decoded["net_edge"] == 0.149
    assert decoded["books"] == 6

    url = approval_links.build_approval_url("https://example.streamlit.app/", token)
    assert url.startswith("https://example.streamlit.app/Approve?trade=")
    assert len(url) < 512

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
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
