from __future__ import annotations

import os
import watcher


def main() -> int:
    message = "\n".join(
        [
            "The two BTC 15-minute Whale Flow alerts have expired.",
            "DO NOT PLACE either BTC bet.",
            "They were resent incorrectly during the app repair after the 12:00 PM ET market close.",
            "Green Bay YES was rechecked at 71¢ and was the only 11:47 alert still actionable.",
            "Tap Open PM Edge for the current live review before submitting.",
        ]
    )
    ok = watcher.send_push(
        "⚠️ PM EDGE WHALE FLOW — CORRECTION",
        message,
        priority=1,
        url=os.getenv("STREAMLIT_APP_URL") or None,
        url_title="Open PM Edge",
    )
    print("WHALE_CORRECTION_SENT" if ok else "WHALE_CORRECTION_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
