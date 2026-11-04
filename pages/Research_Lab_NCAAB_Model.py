"""Research Lab: NCAA Basketball Model (paper $500 bankroll, every game logged at a frozen line). Read-only."""
import sys

import data_sync  # Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits

data_sync.sync()

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k.split(".")[0] in ("ncaab_grader", "ncaab_model")]:
    del sys.modules[_m]

from ncaab_grader.dashboard import render

render()
