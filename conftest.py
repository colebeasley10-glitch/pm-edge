"""Keep tests off the real ledgers: point every ledger path at a temp dir before any import."""
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="pmedge-tests-"))
for _var, _name in (("LIVE_SHARP_LEDGER_PATH", "live.csv"), ("LEAGUE_LEDGER_PATH", "league.csv"),
                    ("LEAGUE_STATUS_PATH", "status.json"), ("LEAGUE_SHADOW_PATH", "shadow.csv"),
                    ("LIVE_SHARP_BALANCE_LOG", "balance.csv")):
    os.environ.setdefault(_var, str(_tmp / _name))
os.environ.setdefault("LEAGUE_PUSH", "0")
