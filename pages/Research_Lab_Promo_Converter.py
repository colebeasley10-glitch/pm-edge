"""Strategy Research Lab: Promo Converter (bonus bet -> cash calculator)."""
import sys

# Streamlit Cloud keeps imported modules in memory across code updates; drop the lab's
# modules so this page always runs the newest Research Lab code (no app reboot needed).
for _m in [k for k in sys.modules if k == "research_lab" or k.startswith("research_lab.")]:
    del sys.modules[_m]

from research_lab.dashboard import render_promo

render_promo()
