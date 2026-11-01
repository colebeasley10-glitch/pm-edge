"""Kickoff Sprint + Prop Board fixes, replayed on Nov 1, 2026 (Hunter's Pickens / Whiteheart cards)."""
from datetime import datetime, timedelta, timezone

from research_lab import kickoff_sprint as ks, odds_fam, pinnacle, prop_board as pb

KICK = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)          # 1:00 PM ET, Cardinals at Cowboys
START = "2026-11-01T18:00:00Z"


def nfl(over, under, *, line=4.5, special="George Pickens (Receptions)", eid=2, start=START, limit=1000):
    return {"events": [
        {"event_id": 1, "league_name": "NFL", "home": "Dallas Cowboys", "away": "Arizona Cardinals", "starts": start,
         "periods": {"num_0": {}}},
        {"event_id": eid, "parent_id": 1, "league_name": "NFL", "special": special, "special_units": "",
         "special_category": "Player Props", "starts": start,
         "special_markets": {"num_0": [{"type": "total", "max_risk": limit, "prices": [
             {"name": "Over", "points": line, "price": over}, {"name": "Under", "points": line, "price": under}]}]}}]}


class Pin:
    key = "x"

    def __init__(self, payloads):
        self.payloads, self.calls = list(payloads), 0

    def fixtures(self, sid):
        self.calls += 1
        return self.payloads.pop(0)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def test_read_times_t9_and_t3():
    now = KICK - timedelta(minutes=13)                             # the 12:47 round
    assert ks.read_times(KICK, now, now) == [KICK - timedelta(minutes=9), KICK - timedelta(minutes=3)]
    now = KICK - timedelta(minutes=8)                              # a 4:17 round before a 4:25 kickoff
    assert ks.read_times(KICK, now, now) == [KICK - timedelta(minutes=3)]
    assert ks.read_times(KICK, KICK - timedelta(minutes=4), KICK - timedelta(minutes=4)) == []


def test_no_sprint_without_a_kickoff_soon(tmp_path):
    pb.update({5: nfl(1.95, 1.87)}, KICK - timedelta(hours=3), tmp_path)
    out = ks.run(now=KICK - timedelta(minutes=40), base=tmp_path, client=Pin([]))
    assert out == {"kickoff": None, "reads": []}


def test_pickens_late_move_is_caught(tmp_path):
    """Our feed had Pickens U4.5 near -114 fair all morning; Pinnacle moved it in the last ~10 min."""
    pushes = []
    for h, at in ((6, None), (2, None)):
        pb.update({5: nfl(1.95, 1.87)}, KICK - timedelta(hours=h), tmp_path)
    t = KICK - timedelta(minutes=13)                              # 12:47 round: still flat
    pb.update({5: nfl(1.95, 1.87)}, t, tmp_path)
    u = pinnacle.load_usage(t, tmp_path)
    u["last_fetch"]["5"] = t.isoformat().replace("+00:00", "Z")
    pinnacle.save_usage(u, tmp_path)
    clock = Clock(t)
    pin = Pin([nfl(1.95, 1.87), nfl(2.30, 1.65)])                  # T-9 flat, T-3 Under -154ish
    out = ks.run(base=tmp_path, client=pin, clock=clock, sleep=clock.sleep,
                 push=lambda ti, m, u: pushes.append((ti, m)) or True)
    assert pin.calls == 2 and len(out["reads"]) == 2
    plays = pb.load(tmp_path)
    assert len(plays) == 1 and plays[0]["side"] == "under" and plays[0]["move"].startswith("⚡")
    fam = odds_fam.load(tmp_path)
    assert fam[0]["pick"] == "George Pickens Under 4.5 Receptions" and fam[0]["pushed"] == "True"
    assert pushes and "Onyx" in pushes[0][1]
    # worst price is fair + 2%: well under Onyx's -118, so the alert would have said "bet it"
    assert odds_fam.american_to_decimal("-118") > float(fam[0]["bet_at_decimal"])


def test_late_window_uses_lower_bar(tmp_path):
    """A ~4% move (just under the normal bar, like Whiteheart's 12:03 move) only counts within 45 minutes of kickoff."""
    early = tmp_path / "early"
    for base, kick_in in ((early, timedelta(hours=3)), (tmp_path / "late", timedelta(minutes=40))):
        t = KICK - kick_in
        pb.update({5: nfl(1.95, 1.87)}, t - timedelta(hours=3), base)
        pb.update({5: nfl(1.95, 1.87)}, t - timedelta(minutes=15), base)
        pb.update({5: nfl(2.11, 1.75)}, t, base)
    assert pb.load(early) == []
    assert len(pb.load(tmp_path / "late")) == 1


def test_day_cap_is_eastern_and_per_round(tmp_path):
    """Ten alerts at 8pm ET Saturday (00:02 UTC Sunday) must not use up Sunday's cap."""
    sat_night = datetime(2026, 11, 1, 0, 2, tzinfo=timezone.utc)
    rows = [{"play_id": f"old{i}", "alert_time": sat_night.isoformat(), "player": f"P{i}", "game": "g",
             "status": "open", "line": 1, "side": "over"} for i in range(10)]
    pb._save(rows, tmp_path)
    t = KICK - timedelta(minutes=30)
    pb.update({5: nfl(1.95, 1.87)}, t - timedelta(hours=3), tmp_path)
    pb.update({5: nfl(1.95, 1.87)}, t - timedelta(minutes=15), tmp_path)
    info = pb.update({5: nfl(2.30, 1.65)}, t, tmp_path)
    assert info["new_plays"] == 1


def test_one_round_burst_is_capped(tmp_path):
    t = KICK - timedelta(hours=4)
    names = [f"Player {i} (Receptions)" for i in range(8)]

    def many(over, under):
        evs = []
        for i, n in enumerate(names):
            evs += nfl(over, under, special=n, eid=10 + i)["events"]
        return {5: {"events": evs}}

    pb.update(many(1.91, 1.91), t, tmp_path)
    info = pb.update(many(2.40, 1.60), t + timedelta(hours=3), tmp_path)
    assert info["new_plays"] == pb.MAX_PER_ROUND
