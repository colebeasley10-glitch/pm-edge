"""Research Lab: Trader Follow (copy-alerts for proven Polymarket bettors). Read-only."""
import sys

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "trader_follow" or k.startswith("trader_follow.")]:
    del sys.modules[_m]

from trader_follow.dashboard import render

render()
