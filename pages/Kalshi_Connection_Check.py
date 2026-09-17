from __future__ import annotations

import os

import streamlit as st

# Bridge Streamlit secrets into the environment expected by KalshiSubmitClient.
for _name in (
    "KALSHI_WRITE_API_KEY_ID",
    "KALSHI_WRITE_PRIVATE_KEY",
    "PM_EDGE_KALSHI_WRITE_BASE_URL",
):
    try:
        if _name in st.secrets:
            os.environ[_name] = str(st.secrets[_name])
    except Exception:
        pass

from kalshi_submit import KalshiSubmitClient, KalshiSubmitError

st.set_page_config(page_title="PM Edge — Kalshi Connection Check", page_icon="🔌", layout="centered")
st.title("PM Edge — Kalshi Connection Check")
st.caption("Safe test only. This page can only read recent orders. It cannot place, modify, or cancel a Kalshi order.")

client = None
try:
    client = KalshiSubmitClient.from_env()
except Exception as exc:
    st.error("Connection setup error")
    st.caption(str(exc))
    st.stop()

if client is None:
    st.error("Not connected yet")
    st.write("Streamlit cannot see the new Kalshi write-key secrets yet.")
    st.stop()

try:
    orders = client.get_orders(limit=1)
except KalshiSubmitError as exc:
    st.error("Kalshi rejected the connection")
    st.write(f"HTTP {exc.status_code}")
    st.caption(str(exc))
except Exception as exc:
    st.error("Connection failed")
    st.caption(f"{type(exc).__name__}: {exc}")
else:
    st.success("CONNECTED ✓")
    st.write("The new Kalshi key authenticated successfully.")
    st.write("No bet was placed.")
    st.caption(f"Read-only connection test returned {len(orders)} recent order row(s).")
