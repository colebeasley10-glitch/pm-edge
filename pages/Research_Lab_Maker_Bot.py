"""Research Lab: Maker Bot ($1 resting Kalshi orders priced better than Pinnacle). Read-only."""
import sys

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "maker_bot" or k.startswith("maker_bot.")]:
    del sys.modules[_m]

from maker_bot.dashboard import render

render()
