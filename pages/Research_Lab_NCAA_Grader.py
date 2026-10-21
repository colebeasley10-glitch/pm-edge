"""Research Lab: NCAA Model Grader (every pick logged at a frozen line, then graded). Read-only."""
import sys

# Streamlit Cloud keeps imported modules in memory across code updates; always load the newest code.
for _m in [k for k in sys.modules if k == "ncaa_grader" or k.startswith("ncaa_grader.")]:
    del sys.modules[_m]

from ncaa_grader.dashboard import render

render()
