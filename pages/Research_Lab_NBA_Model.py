"""Research Lab: NBA Game Model (every pick logged at a frozen line, then graded). Read-only."""
import sys

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "nba_grader" or k.startswith("nba_grader.") or k == "nba_ratings"]:
    del sys.modules[_m]

from nba_grader.dashboard import render

render()
