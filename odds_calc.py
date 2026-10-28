"""Floating odds calculator: a small button pinned to the right side of every SharpIQ page.

Tap it and a popover opens: type American odds to get the implied win % (same as the
Kalshi price in cents), or type a % to get the American odds. The two boxes stay in sync.
Runs as a fragment, so typing in it never reloads the rest of the page.
"""
from __future__ import annotations

import math

import streamlit as st

_ODDS = "oc_odds"
_PCT = "oc_pct"


# ---- math (pure, tested) -------------------------------------------------------------------

def parse_american(text: str) -> float | None:
    """'+150', '150', '-200', 'even' -> float odds; None if not valid American odds."""
    s = str(text or "").strip().lower().replace(" ", "").replace(",", "")
    if s in {"even", "ev", "evens", "pk"}:
        return 100.0
    try:
        a = float(s)
    except ValueError:
        return None
    if not math.isfinite(a) or abs(a) < 100:
        return None
    return a


def american_to_prob(a: float) -> float:
    """American odds -> implied probability (0-1)."""
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def prob_to_american(p: float) -> float:
    """Implied probability (0-1, exclusive) -> American odds. 50% -> +100."""
    if p <= 0.5:
        return 100.0 * (1.0 - p) / p
    return -100.0 * p / (1.0 - p)


def fmt_american(a: float) -> str:
    r = int(round(a))
    if abs(r) == 100:
        return "+100"
    return f"+{r}" if r > 0 else str(r)


# ---- widget --------------------------------------------------------------------------------

def _on_odds() -> None:
    a = parse_american(st.session_state.get(_ODDS, ""))
    if a is not None:
        st.session_state[_PCT] = round(american_to_prob(a) * 100.0, 2)


def _on_pct() -> None:
    p = st.session_state.get(_PCT)
    if p is not None and 0 < p < 100:
        st.session_state[_ODDS] = fmt_american(prob_to_american(p / 100.0))


_CSS = """
<style>
.st-key-oc_fab { position:fixed !important; right:.75rem; bottom:calc(9.5rem + env(safe-area-inset-bottom));
                 z-index:1001; width:auto !important; }
.st-key-oc_fab [data-testid="stPopover"] button, .st-key-oc_fab button {
  width:3.1rem !important; height:3.1rem !important; min-height:0 !important; padding:0 !important;
  border-radius:50% !important; background:var(--gold, #F5B800) !important; border:0 !important;
  box-shadow:0 6px 20px rgba(0,0,0,.5) !important; display:flex !important; align-items:center !important;
  justify-content:center !important; }
.st-key-oc_fab button p { font-size:1.35rem !important; font-weight:800 !important; color:#14110A !important;
                           line-height:1 !important; margin:0 !important; }
.st-key-oc_fab button [data-testid="stIconMaterial"], .st-key-oc_fab button svg { display:none !important; }
.oc-out { display:grid; grid-template-columns:1fr 1fr; gap:.4rem; margin-top:.4rem; }
.oc-out div { background:var(--panel2, #111418); border:1px solid var(--line, #262A31); border-radius:10px;
              padding:.45rem .6rem; }
.oc-out b { display:block; font-size:1.05rem; font-weight:800; font-variant-numeric:tabular-nums; }
.oc-out span { color:var(--muted, #8B93A0); font-size:.7rem; }
</style>
"""


@st.fragment
def _calculator() -> None:
    st.session_state.setdefault(_ODDS, "-110")
    st.session_state.setdefault(_PCT, round(american_to_prob(-110) * 100.0, 2))
    st.markdown(_CSS, unsafe_allow_html=True)
    with st.container(key="oc_fab"):
        with st.popover("%", help="Odds calculator"):
            st.markdown("**Odds calculator**")
            c1, c2 = st.columns(2)
            c1.text_input("American odds", key=_ODDS, on_change=_on_odds, placeholder="+150 or -200")
            c2.number_input("Win %", key=_PCT, on_change=_on_pct, min_value=0.1, max_value=99.9,
                            step=0.5, format="%.2f")
            a = parse_american(st.session_state.get(_ODDS, ""))
            if a is None:
                st.caption("Odds must be +100 or higher, or -100 or lower.")
                return
            p = american_to_prob(a)
            win100 = a if a > 0 else 10000.0 / -a
            st.markdown(
                f'<div class="oc-out">'
                f'<div><b>{1 / p:.3f}</b><span>Decimal odds</span></div>'
                f'<div><b>{p * 100:.1f}¢</b><span>Kalshi price</span></div>'
                f'<div><b>${win100:,.2f}</b><span>$100 bet profits</span></div>'
                f'<div><b>${100 * p / (1 - p):,.2f}</b><span>Risk to win $100</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.caption("Type in either box — the other one updates. The % is the break-even win rate at that price.")


def render() -> None:
    """Pin the calculator button to the page. Never let it break the page it sits on."""
    try:
        _calculator()
    except Exception:
        pass
