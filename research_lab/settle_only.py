"""Settle-only round (Nov 4, 2026): grade finished paper plays without waiting for a full Research Lab round.

Full rounds run every ~15 minutes behind PM Edge Watch, so a play could sit "open" for a long time after
ESPN already called the game final. This job only grades: no scanning, no new plays, no phone alerts.

Lanes it settles (all graded from ESPN finals, the same way the full round does):
  Prop Board, Sharp Plays, Odds Fam (plays, your logged bets, board bets) and the Bet Queue.
Odds Fam price-gap plays still settle from Kalshi's public result, read here too.

Only files whose grades changed are written, so an idle run commits nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from research_lab import bet_queue, config, odds_fam, prop_board, sharp_plays
from research_lab.ledger import LedgerError, now_utc, run_lock


def run(now=None, base: Path | None = None, *, box=None, scores=None, kalshi=None) -> dict:
    now = now or now_utc()
    base = Path(base or config.DATA_DIR)
    if box is None:
        box = prop_board.BoxScores()
    if scores is None:
        from trader_follow.scores import ScoreResolver
        scores = ScoreResolver()
    out: dict = {"at": now.isoformat()}
    with run_lock(base):
        # Prop Board
        try:
            rows = prop_board.load(base)
            n = prop_board.grade_rows(rows, now, box)
            if n:
                prop_board._save(rows, base)
            out["prop_board"] = n
        except Exception as exc:
            out["prop_board"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
        # Sharp Plays
        try:
            rows = sharp_plays.load(base)
            n = sharp_plays.grade_rows(rows, now, scores)
            if n:
                sharp_plays._save(rows, base)
            out["sharp_plays"] = n
        except Exception as exc:
            out["sharp_plays"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
        # Odds Fam: plays (props from box scores, price gaps from Kalshi), then bets riding on them
        try:
            markets = {}
            tickers = odds_fam.open_tickers(base) + odds_fam.bet_tickers(base)
            if tickers:
                try:
                    from research_lab.public_data import PublicKalshi
                    markets = (kalshi or PublicKalshi()).markets(tickers)
                except Exception:
                    markets = {}
            rows = odds_fam.load(base)
            n = odds_fam.grade(rows, now, base, markets, box)
            if n:
                odds_fam._write_csv(odds_fam._dir(base) / "plays.csv", rows, odds_fam.FIELDS)
            out["odds_fam"] = n
            out["odds_fam_bets"] = odds_fam.settle_bets(rows, now, base, scores)
            out["odds_fam_board_bets"] = odds_fam.settle_board_bets(now, base, markets=markets, box=box)
        except Exception as exc:
            out["odds_fam"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
        # Bet Queue last: it reads the grades the lanes above just wrote
        try:
            rows = bet_queue.load(base)
            n = bet_queue.settle(rows, now, bet_queue.ROOT, base)
            if n:
                bet_queue._save(rows, base)
            out["bet_queue"] = n
        except Exception as exc:
            out["bet_queue"] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
    return out


if __name__ == "__main__":
    try:
        print(json.dumps(run(), indent=1))
    except LedgerError as exc:      # a full round is running in this checkout; it settles too
        print(f"skipped: {exc}")
        sys.exit(0)
