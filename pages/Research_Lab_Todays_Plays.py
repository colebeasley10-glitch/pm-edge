"""Strategy Research Lab: Today's Plays (EV_FINDER v2, PAPER-tracked)."""
import sys

import data_sync  # Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits

data_sync.sync()

# Streamlit Cloud keeps imported modules in memory across code updates; drop the lab's
# modules so this page always runs the newest Research Lab code (no app reboot needed).
for _m in [k for k in sys.modules if k == "research_lab" or k.startswith("research_lab.")]:
    del sys.modules[_m]

from research_lab import config
from research_lab.dashboard import render_strategy

render_strategy(config.EV_FINDER_V2)
