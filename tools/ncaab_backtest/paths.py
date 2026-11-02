"""Where the offline backtest keeps its downloaded/derived data (not committed; ~250 MB)."""
import os
DATA = os.environ.get("NCAAB_DATA", os.path.join(os.path.dirname(__file__), "..", "..", "ncaab_data")).rstrip("/") + "/"
os.makedirs(DATA + "raw", exist_ok=True)
os.makedirs(DATA + "lines", exist_ok=True)
