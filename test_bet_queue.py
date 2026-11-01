"""Bet Queue: daily short list from lanes earning their place (paper)."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_lab import bet_queue as bq

NOW = datetime(2026, 11, 1, 19, 0, tzinfo=timezone.utc)


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def lane(name, n=100, roi=0.08, verdict="Too early — 100 more bets", reference=False):
    return {"name": name, "n": n, "roi": roi, "verdict": verdict, "reference": reference}


def prop(i, player="Player", tier="STRONG", result="", start=NOW + timedelta(hours=3), prob=0.6, odds="-110"):
    return {"pick_id": f"p{i}", "kickoff": start.isoformat().replace("+00:00", "Z"), "away": "A", "home": "B",
            "player": f"{player}{i}", "side": f"{player}{i} Under 8.5 Points", "odds": odds, "model_prob": prob,
            "tier": tier, "result": result, "source": "live", "clv": "0.01", "units": ""}


def setup(tmp, props):
    root, lab = tmp / "root", tmp / "lab"
    write(root / "nba_grader/data/props.csv", props)
    lab.mkdir(parents=True, exist_ok=True)
    return root, lab


def test_queues_only_eligible_lanes_with_caps(tmp_path):
    root, lab = setup(tmp_path, [prop(i) for i in range(10)] + [prop(99, tier="NO_EDGE")])
    lanes = [lane("NBA Props — Strong + Lean")]
    info = bq.update(NOW, lab, root=root, lanes=lanes)
    assert info["queued"] == bq.MAX_PER_ROUND
    bq.update(NOW + timedelta(minutes=15), lab, root=root, lanes=lanes)
    rows = bq.load(lab)
    assert len(rows) == bq.MAX_PER_LANE_DAY                      # one lane can't fill the day
    assert all(r["lane"] == "NBA Props — Strong + Lean" for r in rows)
    assert float(rows[0]["stake_kelly"]) <= bq.BANKROLL * bq.KELLY_CAP


def test_bad_or_reference_or_thin_lanes_feed_nothing(tmp_path):
    root, lab = setup(tmp_path, [prop(1)])
    for lanes in ([lane("NBA Props — Strong + Lean", verdict="Losing to the close — not real yet")],
                  [lane("NBA Props — Strong + Lean", reference=True)],
                  [lane("NBA Props — Strong + Lean", n=10)],
                  [lane("NBA Props — Strong + Lean", roi=-0.05)]):
        assert bq.update(NOW, lab, root=root, lanes=lanes)["queued"] == 0


def test_thin_slice_judged_by_parent():
    ok = bq.lane_scores([lane("Follow X"), lane("Follow X — NFL", n=10, roi=0.5),
                         lane("Follow X — NBA", n=70, roi=-0.01)])
    assert "Follow X — NFL" in ok and ok["Follow X — NFL"] == ok["Follow X"]
    assert "Follow X — NBA" not in ok


def test_one_prop_per_player_and_started_games_skipped(tmp_path):
    p1, p2 = prop(1), prop(1)
    p2["pick_id"], p2["side"] = "p1b", "Player1 Under 20.5 Pts & Rebs & Asts"
    late = prop(2, start=NOW + timedelta(minutes=2))
    root, lab = setup(tmp_path, [p1, p2, late])
    bq.update(NOW, lab, root=root, lanes=[lane("NBA Props — Strong + Lean")])
    assert len(bq.load(lab)) == 1


def test_settles_from_source(tmp_path):
    root, lab = setup(tmp_path, [prop(1, odds="+120")])
    lanes = [lane("NBA Props — Strong + Lean")]
    bq.update(NOW, lab, root=root, lanes=lanes)
    write(root / "nba_grader/data/props.csv", [prop(1, odds="+120", result="W")])
    info = bq.update(NOW + timedelta(hours=6), lab, root=root, lanes=lanes)
    r = bq.load(lab)[0]
    assert info["settled"] == 1 and r["status"] == "won" and float(r["pl"]) == 12.0
    assert float(r["pl_kelly"]) > 0 and r["clv"] == "0.01"
