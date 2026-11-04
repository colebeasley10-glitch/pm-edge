"""Research Lab: Trader Follow (copy-alerts for proven Polymarket bettors). Read-only."""
import sys

import data_sync  # Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits

data_sync.sync()

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "trader_follow" or k.startswith("trader_follow.")]:
    del sys.modules[_m]

from trader_follow.dashboard import render

render()
