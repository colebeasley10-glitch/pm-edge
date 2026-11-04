"""Daily report (markdown) and Excel/CSV export for the NCAA basketball paper lane."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ncaab_grader import core

XLSX_PATH = core.DATA_DIR / "ncaab_paper_bets.xlsx"


def tip(v) -> str:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(core.ET).strftime("%a %-I:%M %p ET")
    except ValueError:
        return ""


def _odds(x) -> str:
    try:
        return core.fmt_odds(float(x))
    except (TypeError, ValueError):
        return str(x)


def _confidence(r: dict) -> str:
    """Plain-language uncertainty from what the model actually knows about this game."""
    hg, ag = int(float(r.get("home_games") or 0)), int(float(r.get("away_games") or 0))
    g = min(hg, ag)
    if g < 4:
        base = "LOW — ratings are still mostly the preseason prior"
    elif g < 10:
        base = "MEDIUM — ratings blend prior and this season's games"
    else:
        base = "STANDARD — ratings driven by this season's games"
    return f"{base}; calibrated win prob {float(r['model_prob']) * 100:.1f}% (backtest-shrunk toward the market)"


def _why(r: dict) -> str:
    if r["market"] == "TOTAL":
        return (f"Model total {r['model_line']} vs market {r['market_line']} "
                f"({float(r['edge_pts']):+.1f} pts) from adjusted offense/defense and projected pace.")
    if r["market"] == "SPREAD":
        return (f"Model line {r['model_line']} vs market {r['market_line']} "
                f"({float(r['edge_pts']):+.1f} pts) from opponent-adjusted efficiency and venue.")
    return f"Model {r['model_line']} vs market {r['market_line']}."


def block(r: dict) -> str:
    return "\n".join([
        f"GAME: {r['away']} at {r['home']}" + (" (neutral)" if str(r.get("neutral")) == "True" else ""),
        f"TIPOFF: {tip(r['tipoff'])}",
        "",
        f"SPORTSBOOK LINE: {r['side']} ({_odds(r['odds'])})",
        f"MODEL LINE: {r['model_line']}",
        f"PROJECTED SCORE: {r['proj']}",
        "",
        f"MODEL {'COVER' if r['market'] == 'SPREAD' else 'WIN'} PROBABILITY: {float(r['model_prob']) * 100:.1f}%",
        f"BREAK-EVEN PROBABILITY: {float(r['breakeven']) * 100:.2f}%",
        f"EXPECTED ROI: {float(r['ev']) * 100:+.1f}%",
        "",
        f"RECOMMENDATION: {r['side']}" if r["tier"] == "BET" else f"STATUS: {r['tier']} (not a bet)",
        f"MINIMUM ACCEPTABLE PRICE: {_odds(r['min_odds'])}",
        f"SUGGESTED STAKE: ${float(r.get('stake') or 0):.2f} flat (1% of the $500 start) · ¼-Kelly comparison ${float(r.get('stake_kelly') or 0):.2f}",
        f"CONFIDENCE/UNCERTAINTY: {_confidence(r)}",
        f"FLAGS: {r.get('flags') or '—'}",
        "",
        f"WHY: {_why(r)}",
        f"LOGGED: {r['logged_at']} · model {r['model']}",
    ])


def daily(rows: list[dict], status: dict, card: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(core.ET).date().isoformat()
    open_ = [r for r in rows if not r.get("result") and (core._parse_dt(r.get("tipoff") or "") or now) > now]
    bets = sorted([r for r in open_ if r["tier"] == "BET"], key=lambda r: -float(r["ev"]))
    watch = sorted([r for r in open_ if r["tier"] == "WATCH"], key=lambda r: -float(r["ev"]))
    bank = status.get("bankroll") or core.bankroll(rows, card)
    allbets = [r for r in rows if r.get("tier") == "BET"]
    s = core.stats(allbets, pl="pnl", risk="stake")
    sk = core.stats(allbets, pl="pnl_kelly", risk="stake_kelly")
    out = [f"# NCAA Basketball Model — daily report {today}", "",
           f"Model {card['version']} · PAPER ONLY · run {status.get('at', '')}", ""]
    if status.get("suspended"):
        out += [f"**⚠️ BETS SUSPENDED:** {status['suspended']}. Everything below is research only.", ""]
    enabled = [m for m, r in card["rules"].items() if r["enabled"]]
    out += [f"Markets with a tested rule: {', '.join(enabled) or 'none'} · "
            f"others are WATCH only until the backtest supports them.", "",
            f"**Paper bankroll:** ${bank['balance']:.2f} (start ${bank['start']:.0f}, at risk ${bank['at_risk']:.2f}) · "
            f"¼-Kelly comparison ${bank['kelly_balance']:.2f}",
            f"**Season to date (BET tier):** {s['w']}-{s['l']}-{s['p']} · flat P/L ${s['profit']:+.2f}"
            + (f" ({s['roi'] * 100:+.1f}% on risk)" if s["roi"] is not None else "")
            + f" · ¼-Kelly P/L ${sk['profit']:+.2f}"
            + (f" · beat close {s['beat_close'] * 100:.0f}%" if s["beat_close"] is not None else ""), ""]
    out += [f"## Qualified bets ({len(bets)})", ""]
    if bets:
        for r in bets[:10]:
            out += ["```", block(r), "```", ""]
    else:
        out += ["None. No game cleared a tested threshold — that's a normal day, not a failure.", ""]
    out += [f"## Watch list ({len(watch)}) — positive EV but not a qualified bet", ""]
    for r in watch[:12]:
        out.append(f"- {r['side']} ({_odds(r['odds'])}) · {r['away']} at {r['home']} · {tip(r['tipoff'])} · "
                   f"EV {float(r['ev']) * 100:+.1f}% · min {_odds(r['min_odds'])}")
    if not watch:
        out.append("- none")
    ht = status.get("home_test") or {}
    if ht:
        hb, hr = ht.get("bankroll", {}), ht.get("record", {})
        out += ["", "## Home-court spread TEST (paper, own $500 — not bets)", "",
                f"Bankroll ${hb.get('balance', 0):.2f} · {hr.get('w', 0)}-{hr.get('l', 0)}-{hr.get('p', 0)} · "
                f"logged this run {ht.get('logged', 0)} · open {hr.get('open', 0)}"]
    out += ["", "## Data gaps", ""]
    for x in status.get("insufficient") or []:
        out.append(f"- insufficient data: {x}")
    nl = status.get("no_line") or []
    if nl:
        out.append(f"- no line posted yet ({len(nl)}): " + ", ".join(nl[:15]) + (" …" if len(nl) > 15 else ""))
    out.append(f"- possession data coverage: {status.get('possession_coverage', '—')}")
    out += ["", "Injuries, suspensions and lineup news are NOT in the model. The market knows them; "
                "a big model-vs-line gap on a news day is usually the model being wrong."]
    return "\n".join(out)


def export_excel(rows: list[dict], status: dict, card: dict, path: Path | None = None) -> str:
    """Bets + summary + ratings workbook. Falls back to CSV only (picks.csv) if openpyxl is missing."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return "openpyxl not installed — CSV only"
    wb = Workbook()
    ws = wb.active
    ws.title = "Bets"
    ws.append(core.FIELDS)
    for r in sorted(rows, key=lambda r: str(r.get("tipoff"))):
        ws.append([r.get(k, "") for k in core.FIELDS])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F2937")
    ws.freeze_panes = "A2"
    sm = wb.create_sheet("Summary")
    bets = [r for r in rows if r.get("tier") == "BET"]
    sm.append(["Group", "Settled", "W", "L", "P", "P/L $", "ROI", "Beat close", "Open"])
    groups = [("BET — flat $5", bets, "pnl", "stake"), ("BET — ¼ Kelly (comparison)", bets, "pnl_kelly", "stake_kelly")]
    for m in ("SPREAD", "TOTAL", "ML"):
        groups.append((f"BET — {m}", [r for r in bets if r["market"] == m], "pnl", "stake"))
    groups.append(("All logged (1u flat, research)", rows, "units", None))
    for name, sel, pl, risk in groups:
        s = core.stats(sel, pl=pl, risk=risk)
        sm.append([name, s["picks"], s["w"], s["l"], s["p"], s["profit"],
                   None if s["roi"] is None else round(s["roi"], 4),
                   None if s["beat_close"] is None else round(s["beat_close"], 4), s["open"]])
    sm.append([])
    sm.append(["Period", "Key", "P/L $ (flat)"])
    for k, v in core.periods(rows).items():
        for kk, vv in v.items():
            sm.append([k, kk, vv])
    rt = wb.create_sheet("Ratings")
    rt.append(["team", "adj_o", "adj_d", "net", "tempo", "games", "prior_net"])
    for r in status.get("ratings") or []:
        rt.append([r.get(k) for k in ("team", "adj_o", "adj_d", "net", "tempo", "games", "prior_net")])
    (path or XLSX_PATH).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path or XLSX_PATH)
    return str(path or XLSX_PATH)
