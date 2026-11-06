"""Phone alerts for the NCAA pool cards.

Nov 4, 2026: started as a Saturday-morning-only re-check against the picks locked in SharpIQ.
Nov 5, 2026: Cole missed Southern Miss +14.5 (a Thursday-night game) because nothing told him it was on the
card before kickoff. Alerts now run all week, every time the NCAA grader workflow ticks (~15 min):

  1. Card alert -- the first time a week's card exists, and again whenever a pick is added or dropped or the
     keys change. Compared against the picks he locked in SharpIQ if he has, otherwise against the last
     card he was sent. One alert per distinct card; quiet 12am-7am ET (changes made overnight go out at 7am).
  2. Early-game alert -- a card pick whose game kicks off before Saturday (Thu/Fri night) gets its own alert
     about 3 hours before kickoff, so it can be entered in the pool before that game locks. Once per pick.
     Not sent if that pick is already in his locked SharpIQ picks or locked on its own ("Lock this pick").
  3. Saturday reminder -- if a pool still isn't locked in SharpIQ on Saturday morning, one reminder with the keys.

Picks in early games are flagged with their kickoff time in every alert. Paper only: nothing here bets.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from ncaa_pool import weekly as wk

STATE = wk.DATA / "recheck_alerts.json"
START_HOUR_ET = 9                 # Saturday "not locked" reminder window starts
QUIET_START_ET, QUIET_END_ET = 0, 7
EARLY_LEAD = timedelta(hours=3)
POOL_NAMES = {"clay": "Clay's pool", "chappy": "Chappy's pool"}


def _load() -> dict:
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except ValueError:
        return {}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def push(title: str, message: str) -> bool:
    token, user = os.getenv("PUSHOVER_APP_TOKEN", "").strip(), os.getenv("PUSHOVER_USER_KEY", "").strip()
    if not token or not user:
        print(f"[no Pushover keys] {title}: {message}")
        return False
    import requests
    try:
        r = requests.post("https://api.pushover.net/1/messages.json", timeout=15,
                          data={"token": token, "user": user, "title": title[:250], "message": message[:1024]})
        return r.status_code == 200
    except requests.RequestException as exc:
        print(f"Pushover failed: {exc}")
        return False


def _kick(r: dict) -> datetime | None:
    return wk._dt(r.get("kickoff"))


def _is_early(r: dict) -> bool:
    """Thursday/Friday games (kick off before Saturday ET)."""
    k = _kick(r)
    return bool(k) and k.astimezone(wk.ET).weekday() in (0, 1, 2, 3, 4)


def _when(r: dict) -> str:
    k = _kick(r)
    if not k:
        return ""
    e = k.astimezone(wk.ET)
    return f"{e:%a} {e:%I:%M%p}".replace(" 0", " ").replace("AM", "am").replace("PM", "pm")


def _line(r: dict) -> str:
    try:
        s = f"{r['pick']} ({float(r['prob']) * 100:.0f}%, mkt {float(r['market']):+g})"
    except (KeyError, TypeError, ValueError):
        s = r.get("pick", "?")
    if r.get("frozen"):
        return s + " (already kicked off)"
    return s + (f" ⏰ {_when(r)}" if _is_early(r) else "")


def tb_line(tb: dict | None) -> str:
    """Nov 5, 2026: the tiebreaker guess rides along on every alert."""
    if not tb:
        return ""
    return f"\nTiebreaker {tb['away']} @ {tb['home']}: enter {tb['guess']} (mkt total {tb['total']:g})."


def _sig(rows: list[dict]) -> str:
    return "|".join(sorted(r["pick"] for r in rows)) + "#" + "|".join(sorted(
        r["pick"] for r in rows if r.get("key") == "Y"))


def card_text(model: list[dict]) -> str:
    rows = sorted(model, key=lambda r: int(r.get("rank") or 99))
    out = [f"{r.get('rank')}. {_line(r)}" + (" KEY" if r.get("key") == "Y" else "") for r in rows]
    exp, mx = wk.expected_points(model)
    return "\n".join(out) + f"\nExp. {exp:.1f} of {mx} pts."


def message(pool: str, mine: list[dict], model: list[dict], tb: dict | None = None,
            locked: bool = True) -> tuple[str, str] | None:
    """Alert text when the model's card differs from `mine` (locked entry or last card sent), else None."""
    d = wk.diff(mine, model)
    if not d["dropped"] and not d["added"] and not d["keys_changed"]:
        return None
    by_pick = {r["pick"]: r for r in model}
    lines = []
    if d["added"]:
        lines.append("Add: " + "; ".join(_line(by_pick[p]) for p in d["added"]))
    if d["dropped"]:
        lines.append("Drop: " + "; ".join(d["dropped"]))
    if d["keys_changed"]:
        lines.append("Keys now: " + "; ".join(d["model_keys"]))
    exp_model, mx = wk.expected_points(model)
    if locked:
        exp_mine, _ = wk.expected_points([{**r, "prob": by_pick.get(r["pick"], r).get("prob")} for r in mine])
        lines.append(f"Exp. points: yours {exp_mine:.1f} → model {exp_model:.1f} of {mx}.")
        title = f"{POOL_NAMES[pool]}: card moved off your locked picks"
    else:
        lines.append(f"Exp. {exp_model:.1f} of {mx} pts. Full card in SharpIQ.")
        title = f"{POOL_NAMES[pool]}: card changed"
    return title, "\n".join(lines) + tb_line(tb)


def run(now: datetime | None = None, send=push) -> list[str]:
    now = now or datetime.now(timezone.utc)
    et = now.astimezone(wk.ET)
    quiet = QUIET_START_ET <= et.hour < QUIET_END_ET
    state, sent = _load(), []
    for pool, season, week, _path in wk.slates():
        model = wk._csv(wk.card_path(pool, season, week))
        if not model or all(r.get("frozen") for r in model):
            continue
        kicks = sorted(filter(None, (_kick(r) for r in model)))
        if not kicks or kicks[-1] < now:
            continue                                   # an old week's card
        key = f"{pool}_{season}_w{week}"
        mine = wk._csv(wk.mine_path(pool, season, week))
        tb = wk.load_tiebreaker(pool, season, week)
        mine_picks = {r["pick"] for r in mine} | {r["pick"] for r in wk.load_held(pool, season, week)}

        # 1. card alerts (new card, or a change)
        if not quiet:
            sig = _sig(model) + ("@" + _sig(mine) if mine else "")
            if mine:
                msg = message(pool, mine, model, tb, locked=True)
            elif not state.get(key + "_card"):
                msg = (f"{POOL_NAMES[pool]}: week {week} card is up", card_text(model) + tb_line(tb))
            else:
                last = [{"pick": p, "key": "Y" if p in state.get(key + "_keys", []) else "N"}
                        for p in state[key + "_card"]]
                msg = message(pool, last, model, tb, locked=False)
            if msg and state.get(key) != sig and send(*msg):
                state[key] = sig
                sent.append(key)
            if mine or msg is None or state.get(key) == sig:
                state[key + "_card"] = sorted(r["pick"] for r in model)
                state[key + "_keys"] = sorted(r["pick"] for r in model if r.get("key") == "Y")

        # 2. early-game heads-up, ~3h before a Thu/Fri kickoff
        warned = set(state.get(key + "_early", []))
        for r in model:
            k = _kick(r)
            if (r.get("frozen") or not _is_early(r) or not k or r["pick"] in warned
                    or r["pick"] in mine_picks or not (k - EARLY_LEAD <= now < k)):
                continue
            mins = int((k - now).total_seconds() // 60)
            body = (f"{_line(r)}{' — KEY (double points)' if r.get('key') == 'Y' else ''}\n"
                    f"Kicks off in {mins // 60}h {mins % 60:02d}m — enter it in {POOL_NAMES[pool]} before then.")
            if send(f"{POOL_NAMES[pool]}: early game on the card", body):
                warned.add(r["pick"])
                sent.append(f"{key}_early:{r['pick']}")
        state[key + "_early"] = sorted(warned)

        # 3. Saturday morning: not locked in SharpIQ yet
        if (not mine and et.weekday() == 5 and START_HOUR_ET <= et.hour < wk.LOCK_HOUR_ET
                and not state.get(key + "_reminder")):
            keys = [r["pick"] for r in model if r.get("key") == "Y"]
            exp, mx = wk.expected_points(model)
            if send(f"{POOL_NAMES[pool]}: not locked in SharpIQ",
                    f"Model keys: {'; '.join(keys)}. Exp. {exp:.1f} of {mx} pts. Lock by noon." + tb_line(tb)):
                state[key + "_reminder"] = now.isoformat(timespec="minutes")
                sent.append(key + "_reminder")
    _save(state)
    return sent


if __name__ == "__main__":
    print(run())
