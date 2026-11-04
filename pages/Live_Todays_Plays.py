"""Live $5 Today's Plays: real Kalshi bets + Polymarket alerts, with the paper record. Read-only."""
import sys

import data_sync  # Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits

data_sync.sync()

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "live_plays" or k.startswith("live_plays.")]:
    del sys.modules[_m]

from live_plays.dashboard import render

render()
