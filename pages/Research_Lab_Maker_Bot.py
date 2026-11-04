"""Research Lab: Maker Bot ($1 resting Kalshi orders priced better than Pinnacle). Read-only."""
import sys

import data_sync  # Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits

data_sync.sync()

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "maker_bot" or k.startswith("maker_bot.")]:
    del sys.modules[_m]

from maker_bot.dashboard import render

render()
