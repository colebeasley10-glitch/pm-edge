"""Live $5 Today's Plays: real Kalshi bets + Polymarket alerts, with the paper record. Read-only."""
import sys

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "live_plays" or k.startswith("live_plays.")]:
    del sys.modules[_m]

from live_plays.dashboard import render

render()
