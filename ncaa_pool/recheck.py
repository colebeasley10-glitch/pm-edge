"""Saturday-morning re-check for the NCAA pools (Nov 4, 2026).

Cole usually locks his pool picks Friday around 8pm, but lines keep moving into Saturday (injuries, weather,
sharp money). Between 9am and noon ET on Saturday this compares the picks he locked in SharpIQ with the
model's current card (Pinnacle line) and sends ONE phone alert per change -- only when the card is different
from what he locked, and only when it's different from what was already alerted. No change, no alert.
If he hasn't locked a pool in the app by then, it sends the model's keys once as a reminder.

Runs after the hourly pool snapshot in the NCAA grader workflow. Paper only: nothing here bets.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ncaa_pool import weekly as wk

STATE = wk.DATA / "recheck_alerts.json"
START_HOUR_ET = 9
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


def _line(r: dict) -> str:
    try:
        return f"{r['pick']} ({float(r['prob']) * 100:.0f}%, mkt {float(r['market']):+g})"
    except (KeyError, TypeError, ValueError):
        return r.get("pick", "?")


def tb_line(tb: dict | None) -> str:
    """Nov 5, 2026: the tiebreaker guess rides along on every alert."""
    if not tb:
        return ""
    return f"\nTiebreaker {tb['away']} @ {tb['home']}: enter {tb['guess']} (mkt total {tb['total']:g})."


def message(pool: str, mine: list[dict], model: list[dict], tb: dict | None = None) -> tuple[str, str] | None:
    """Alert text when the model's card differs from the locked entry, else None."""
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
    exp_mine, mx = wk.expected_points([{**r, "prob": by_pick.get(r["pick"], r).get("prob")} for r in mine])
    exp_model, _ = wk.expected_points(model)
    lines.append(f"Exp. points: yours {exp_mine:.1f} → model {exp_model:.1f} of {mx}. Picks lock at noon.")
    return f"{POOL_NAMES[pool]}: lines moved — re-check", "\n".join(lines) + tb_line(tb)


def run(now: datetime | None = None, send=push) -> list[str]:
    now = now or datetime.now(timezone.utc)
    et = now.astimezone(wk.ET)
    if et.weekday() != 5 or not (START_HOUR_ET <= et.hour < wk.LOCK_HOUR_ET):
        return []
    state, sent = _load(), []
    for pool, season, week, _path in wk.slates():
        cp = wk.card_path(pool, season, week)
        model = wk._csv(cp)
        if not model or all(r.get("frozen") for r in model):
            continue
        lock_date = et.date().isoformat()
        kick = sorted(filter(None, (wk._dt(r.get("kickoff")) for r in model)))
        if not kick or kick[-1].astimezone(wk.ET).date().isoformat() < lock_date:
            continue                                   # an old week's card
        key = f"{pool}_{season}_w{week}"
        mine = wk._csv(wk.mine_path(pool, season, week))
        tb = wk.load_tiebreaker(pool, season, week)
        if mine:
            msg = message(pool, mine, model, tb)
            if msg is None:
                continue
            sig = "|".join(sorted(r["pick"] for r in model)) + "#" + "|".join(sorted(
                r["pick"] for r in model if r.get("key") == "Y"))
            if state.get(key) == sig:
                continue
            if send(*msg):
                state[key] = sig
                sent.append(key)
        elif not state.get(key + "_reminder"):
            keys = [r["pick"] for r in model if r.get("key") == "Y"]
            exp, mx = wk.expected_points(model)
            if send(f"{POOL_NAMES[pool]}: not locked in SharpIQ",
                    f"Model keys: {'; '.join(keys)}. Exp. {exp:.1f} of {mx} pts. Lock by noon." + tb_line(tb)):
                state[key + "_reminder"] = now.isoformat(timespec="minutes")
                sent.append(key + "_reminder")
    if sent:
        _save(state)
    return sent


if __name__ == "__main__":
    print(run())
