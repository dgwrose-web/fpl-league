#!/usr/bin/env python3
"""Build docs/data.json for the FPL mini-league hub.

Run: python3 scripts/build.py [--config config.json] [--out docs/data.json]

Everything the site shows is derived here so the browser only ever loads one
static JSON file - no CORS proxy, no API keys, no server.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict  # noqa: F401
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fpl_client import FPLClient  # noqa: E402

UK = ZoneInfo("Europe/London")
ROOT = Path(__file__).resolve().parent.parent

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


# --------------------------------------------------------------------- helpers

def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def to_uk(dt: datetime | None) -> datetime | None:
    return dt.astimezone(UK) if dt else None


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def rank_with_ties(rows: list[dict], key: str, reverse: bool = True) -> None:
    """Assign a 'rank' field, sharing rank on ties (1,2,2,4)."""
    rows.sort(key=lambda r: r[key], reverse=reverse)
    last_val, last_rank = None, 0
    for i, r in enumerate(rows, start=1):
        if r[key] != last_val:
            last_rank = i
            last_val = r[key]
        r["rank"] = last_rank


# ------------------------------------------------------------ month assignment

def build_months(events: list[dict], fixtures: list[dict], overrides: dict) -> list[dict]:
    """Assign each gameweek, whole and undivided, to one calendar month.

    The rule: a gameweek belongs to the month in which MOST of its fixtures are
    played (ties broken by the deadline's month). A gameweek is never split
    across two months, which is what makes the awkward cases work:

      * GW5 finishing Sun 20 Sep - all fixtures in September, so September.
      * GW9 with fixtures on Sat 31 Oct, Sun 1 Nov and Mon 2 Nov - the majority
        are played on the Saturday, so the whole gameweek counts for October,
        exactly as FPL's own Manager of the Month table treats it.

    Gameweeks with no scheduled fixtures yet (TV picks not made) fall back to
    the month of their deadline. `month_overrides` in config.json wins over
    everything if FPL ever publishes a split that disagrees.
    """
    kickoffs: dict[int, list[datetime]] = defaultdict(list)
    last_kickoff: dict[int, datetime] = {}
    for f in fixtures:
        ev, ko = f.get("event"), parse_dt(f.get("kickoff_time"))
        if ev and ko:
            ko_uk = to_uk(ko)
            kickoffs[ev].append(ko_uk)
            if ev not in last_kickoff or ko_uk > last_kickoff[ev]:
                last_kickoff[ev] = ko_uk

    buckets: dict[str, list[int]] = defaultdict(list)
    assignment: dict[int, str] = {}
    for ev in events:
        gw = ev["id"]
        dl = to_uk(parse_dt(ev.get("deadline_time")))
        deadline_key = f"{dl.year:04d}-{dl.month:02d}" if dl else None

        if kickoffs.get(gw):
            tally = Counter(f"{k.year:04d}-{k.month:02d}" for k in kickoffs[gw])
            best = max(tally.values())
            leaders = sorted(k for k, v in tally.items() if v == best)
            key = deadline_key if (len(leaders) > 1 and deadline_key in leaders) else leaders[0]
        else:
            key = deadline_key
        if not key:
            continue
        assignment[gw] = key
        buckets[key].append(gw)

    # Manual overrides: {"2026-09": [3,4,5]}
    for key, gws in (overrides or {}).items():
        buckets[key] = list(gws)

    by_id = {e["id"]: e for e in events}
    months = []
    for key in sorted(buckets):
        gws = sorted(buckets[key])
        year, mon = int(key[:4]), int(key[5:])
        evs = [by_id[g] for g in gws if g in by_id]
        complete = bool(evs) and all(e.get("finished") and e.get("data_checked") for e in evs)
        final_ko = max((last_kickoff[g] for g in gws if g in last_kickoff), default=None)
        publish = (final_ko + timedelta(days=1)).date().isoformat() if final_ko else None
        months.append({
            "key": key,
            "label": f"{MONTH_NAMES[mon - 1]} {year}",
            "short": MONTH_NAMES[mon - 1],
            "gws": gws,
            "complete": complete,
            "started": any(e.get("finished") for e in evs),
            "final_kickoff": final_ko.isoformat() if final_ko else None,
            "publish_date": publish,
        })
    return months


# ------------------------------------------------- per-player attribution

# Compact row format: one array per manager per gameweek, in this order. Arrays
# rather than objects because 20 managers x 38 gameweeks of named keys would
# quadruple the size of data.json for no benefit - the page maps them back.
STAT_KEYS = ["disc", "yc", "rc", "goals", "assists", "defcon", "bonus",
             "dbl", "hat", "p_gk", "p_def", "p_mid", "p_fwd"]

POS_SLOT = {1: "p_gk", 2: "p_def", 3: "p_mid", 4: "p_fwd"}


def _defcon_raw(stats: dict):
    """Return (value, source_key) for defensive contribution, or (None, None).

    FPL added Defensive Contribution in 2025/26 and the API field has moved
    once already, so try the known spellings and fall back to summing the
    component actions. Better a derived number than a silently empty table.
    """
    for k in ("defensive_contribution", "defensive_contributions", "defcon"):
        v = stats.get(k)
        if v is not None:
            return float(v), k
    total, seen = 0.0, False
    for k in ("clearances_blocks_interceptions", "tackles", "recoveries"):
        v = stats.get(k)
        if v is not None:
            total += float(v)
            seen = True
    return (total, "derived") if seen else (None, None)


def build_player_tables(managers: list[dict], picks_by_gw: dict, live_stats: dict,
                        elements: dict, config: dict) -> dict:
    """Attribute individual player performances to the manager who owned them.

    By default only players who actually lined up count: `multiplier > 0` means
    "this player counted for you", and because FPL rewrites the multiplier once
    auto-subs are applied, substitutes are handled for free. A red card on
    someone's bench cost them nothing, so it shouldn't be held against them -
    but `squad_scope: "all_15"` in config.json switches that round.
    """
    hcfg = (config.get("hall") or {})
    scope_all = str(hcfg.get("squad_scope", "starting_xi")).lower() in ("all_15", "all", "squad")
    use_mult = bool(hcfg.get("captain_multiplier", False))
    threshold = int(hcfg.get("double_figure_threshold", 10))

    # Is the API giving us DEFCON points (0 or 2) or a raw count of actions?
    # Decide once from the season's spread rather than guessing per player.
    seen_vals, field = [], None
    for gw_stats in live_stats.values():
        for st in gw_stats.values():
            v, k = _defcon_raw(st)
            if v is not None:
                seen_vals.append(v)
                field = field or k
    if not seen_vals:
        mode = "unavailable"
    elif max(seen_vals) <= 3:
        mode = "points"
    else:
        mode = "count"

    def defcon_points(st: dict, etype: int) -> int:
        if mode == "unavailable" or etype == 1:
            return 0
        v, _ = _defcon_raw(st)
        if v is None:
            return 0
        if mode == "points":
            return int(v)
        return 2 if v >= (10 if etype == 2 else 12) else 0

    rows = []
    for m in managers:
        entry_id = m["entry"]
        per_gw: dict[str, list[int]] = {}
        for gw, by_entry in picks_by_gw.items():
            pick_set = by_entry.get(entry_id)
            gw_stats = live_stats.get(gw)
            if not pick_set or not gw_stats:
                continue
            acc = dict.fromkeys(STAT_KEYS, 0)
            for p in pick_set.get("picks", []) or []:
                mult = p.get("multiplier", 0) or 0
                if not scope_all and mult <= 0:
                    continue
                st = gw_stats.get(p["element"])
                if not st:
                    continue
                etype = (elements.get(p["element"]) or {}).get("element_type", 0)
                # Counts (goals, cards) are never multiplied - you cannot score
                # one and a half goals. Only points-shaped stats can double.
                w = mult if (use_mult and mult > 0) else 1

                yellow = int(st.get("yellow_cards", 0) or 0)
                red = int(st.get("red_cards", 0) or 0)
                goals = int(st.get("goals_scored", 0) or 0)
                points = int(st.get("total_points", 0) or 0)

                acc["yc"] += yellow
                acc["rc"] += red
                acc["disc"] += yellow + red * 3
                acc["goals"] += goals
                acc["assists"] += int(st.get("assists", 0) or 0)
                acc["bonus"] += int(st.get("bonus", 0) or 0) * w
                acc["defcon"] += defcon_points(st, etype) * w
                if goals >= 3:
                    acc["hat"] += 1
                if points >= threshold:
                    acc["dbl"] += 1
                slot = POS_SLOT.get(etype)
                if slot:
                    acc[slot] += points * w
            if any(acc.values()):
                per_gw[str(gw)] = [acc[k] for k in STAT_KEYS]
        rows.append({"entry": entry_id, "manager": m["manager"],
                     "team": m["team"], "per_gw": per_gw})

    return {
        "available": any(r["per_gw"] for r in rows),
        "keys": STAT_KEYS,
        "gws": sorted({int(g) for r in rows for g in r["per_gw"]}),
        "scope": "all_15" if scope_all else "starting_xi",
        "captain_multiplier": use_mult,
        "double_figure_threshold": threshold,
        "defcon": {"mode": mode, "field": field},
        "managers": rows,
    }


# ----------------------------------------------------------------- main build

def build(config: dict, client: FPLClient) -> dict:
    league_id = config["league_id"]
    basis = config.get("motm_basis", "net")

    boot = client.bootstrap()
    events = boot["events"]
    elements = {e["id"]: e for e in boot["elements"]}
    teams = {t["id"]: t for t in boot["teams"]}
    fixtures = client.fixtures()

    def pname(eid: int) -> str:
        e = elements.get(eid)
        return e["web_name"] if e else f"#{eid}"

    def pteam(eid: int) -> str:
        e = elements.get(eid)
        return teams.get(e["team"], {}).get("short_name", "") if e else ""

    months = build_months(events, fixtures, config.get("month_overrides"))
    finished_gws = [e["id"] for e in events if e.get("finished") and e.get("data_checked")]
    current_gw = max(finished_gws) if finished_gws else 0

    # ----- league standings
    standings = client.league_standings(league_id)
    league_meta = standings.get("league", {}) or {}
    results = standings.get("standings", {}).get("results", []) or []
    new_entries = (standings.get("new_entries", {}) or {}).get("results", []) or []

    # Before GW1 the standings list is empty but new_entries holds the joiners.
    roster = results or [
        {"entry": n["entry"], "player_name": f"{n.get('player_first_name','')} "
                                             f"{n.get('player_last_name','')}".strip(),
         "entry_name": n.get("entry_name", ""), "total": 0, "rank": 0, "last_rank": 0,
         "event_total": 0}
        for n in new_entries
    ]

    # ----- live element points per finished gameweek (for captain scoring)
    live_points: dict[int, dict[int, int]] = {}
    live_stats: dict[int, dict[int, dict]] = {}
    for gw in finished_gws:
        data = client.live(gw, finished=True)
        if data:
            live_stats[gw] = {el["id"]: (el.get("stats") or {}) for el in data.get("elements", [])}
            live_points[gw] = {eid: st.get("total_points", 0) for eid, st in live_stats[gw].items()}

    # ----- per-manager history and picks
    managers: list[dict] = []
    picks_by_gw: dict[int, dict[int, dict]] = defaultdict(dict)

    for row in roster:
        eid = row["entry"]
        hist = client.entry_history(eid)
        chips = {c["event"]: c["name"] for c in hist.get("chips", []) or []}
        gw_rows = []
        for h in hist.get("current", []) or []:
            gw = h["event"]
            gross = h.get("points", 0)
            hit = h.get("event_transfers_cost", 0)
            entry_rec = {
                "gw": gw,
                "gross": gross,
                "hits": hit,
                "net": gross - hit,
                "total": h.get("total_points", 0),
                "bench": h.get("points_on_bench", 0),
                "transfers": h.get("event_transfers", 0),
                "overall_rank": h.get("overall_rank"),
                "value": round(h.get("value", 0) / 10, 1),
                "bank": round(h.get("bank", 0) / 10, 1),
                "chip": chips.get(gw),
                "captain": None,
                "vice": None,
                "captain_points": None,
            }
            picks = client.entry_picks(eid, gw, finished=gw in finished_gws)
            if picks:
                picks_by_gw[gw][eid] = picks
                for p in picks.get("picks", []) or []:
                    if p.get("is_captain"):
                        entry_rec["captain"] = pname(p["element"])
                        entry_rec["captain_id"] = p["element"]
                        base = live_points.get(gw, {}).get(p["element"])
                        if base is not None:
                            entry_rec["captain_points"] = base * max(p.get("multiplier", 2), 1)
                            entry_rec["captain_base"] = base
                    elif p.get("is_vice_captain"):
                        entry_rec["vice"] = pname(p["element"])
            gw_rows.append(entry_rec)

        managers.append({
            "entry": eid,
            "manager": row.get("player_name", "").strip(),
            "team": row.get("entry_name", "").strip(),
            "total": row.get("total", 0),
            "rank": row.get("rank", 0),
            "last_rank": row.get("last_rank", 0),
            "chips_used": [{"gw": gw, "name": n} for gw, n in sorted(chips.items())],
            "history": gw_rows,
        })

    # ----- running league position per gameweek (the rank race)
    for gw in finished_gws:
        snapshot = []
        for m in managers:
            rec = next((h for h in m["history"] if h["gw"] == gw), None)
            if rec:
                snapshot.append({"entry": m["entry"], "total": rec["total"]})
        rank_with_ties(snapshot, "total")
        pos = {s["entry"]: s["rank"] for s in snapshot}
        for m in managers:
            rec = next((h for h in m["history"] if h["gw"] == gw), None)
            if rec:
                rec["league_pos"] = pos.get(m["entry"])

    # ----- season aggregates
    for m in managers:
        h = m["history"]
        m["season"] = {
            "bench_total": sum(x["bench"] for x in h),
            "hits_total": sum(x["hits"] for x in h),
            "transfers_total": sum(x["transfers"] for x in h),
            "captain_points": sum(x["captain_points"] or 0 for x in h),
            "best_gw": max((x["net"] for x in h), default=0),
            "worst_gw": min((x["net"] for x in h), default=0),
            "team_value": h[-1]["value"] if h else 100.0,
        }

    # ----- monthly tables / manager of the month
    for mo in months:
        gws = set(mo["gws"])
        table = []
        for m in managers:
            rows = [x for x in m["history"] if x["gw"] in gws]
            if not rows:
                continue
            gross = sum(x["gross"] for x in rows)
            hits = sum(x["hits"] for x in rows)
            table.append({
                "entry": m["entry"], "manager": m["manager"], "team": m["team"],
                "gross": gross, "hits": hits, "net": gross - hits,
                "bench": sum(x["bench"] for x in rows),
                "transfers": sum(x["transfers"] for x in rows),
                "chips": [x["chip"] for x in rows if x["chip"]],
                "gw_scores": {x["gw"]: x["net"] for x in rows},
            })
        rank_with_ties(table, basis)
        mo["table"] = table
        mo["winner"] = table[0] if table and mo["complete"] else None
        mo["tied"] = bool(table) and len([r for r in table if r["rank"] == 1]) > 1
        if table:
            vals = [r[basis] for r in table]
            mo["stats"] = {
                "avg": round(statistics.fmean(vals), 1),
                "best": max(vals),
                "worst": min(vals),
                "total_hits": sum(r["hits"] for r in table),
                "total_bench": sum(r["bench"] for r in table),
            }

    published = [m for m in months if m["complete"]]
    latest_month = published[-1] if published else None

    player_tables = build_player_tables(managers, picks_by_gw, live_stats,
                                       elements, config)

    # ----- hall of shame (season-long, plus latest month)
    def top_of(seq, key, n=5, reverse=True):
        return sorted(seq, key=key, reverse=reverse)[:n]

    worst_captains = []
    biggest_benchings = []
    worst_hits = []
    for m in managers:
        for x in m["history"]:
            if x.get("captain") and x.get("captain_base") is not None:
                worst_captains.append({
                    "gw": x["gw"], "manager": m["manager"], "team": m["team"],
                    "player": x["captain"], "points": x["captain_base"],
                    "returned": x["captain_points"],
                })
            if x["bench"] >= 8:
                biggest_benchings.append({
                    "gw": x["gw"], "manager": m["manager"], "team": m["team"],
                    "points": x["bench"],
                })
            if x["hits"] > 0:
                worst_hits.append({
                    "gw": x["gw"], "manager": m["manager"], "team": m["team"],
                    "hits": x["hits"], "net": x["net"], "gross": x["gross"],
                })

    hall = {
        "captain_flops": top_of(worst_captains, lambda r: (-r["points"], -r["gw"]))[:8],
        "bench_disasters": top_of(biggest_benchings, lambda r: r["points"])[:8],
        "hit_regrets": top_of(worst_hits, lambda r: r["hits"])[:8],
        "bench_season": top_of(
            [{"manager": m["manager"], "team": m["team"], "points": m["season"]["bench_total"]}
             for m in managers], lambda r: r["points"])[:5],
        "hits_season": top_of(
            [{"manager": m["manager"], "team": m["team"], "points": m["season"]["hits_total"]}
             for m in managers], lambda r: r["points"])[:5],
    }

    # ----- differentials & template (latest finished gameweek)
    differentials = {"gw": current_gw, "template": [], "unique": [], "captaincy": []}
    if current_gw and picks_by_gw.get(current_gw):
        gw_picks = picks_by_gw[current_gw]
        owner_count: Counter[int] = Counter()
        starter_count: Counter[int] = Counter()
        cap_count: Counter[int] = Counter()
        owners: dict[int, list[str]] = defaultdict(list)
        name_of = {m["entry"]: m["manager"] for m in managers}
        for eid, pk in gw_picks.items():
            for p in pk.get("picks", []) or []:
                owner_count[p["element"]] += 1
                owners[p["element"]].append(name_of.get(eid, "?"))
                if p.get("multiplier", 0) > 0:
                    starter_count[p["element"]] += 1
                if p.get("is_captain"):
                    cap_count[p["element"]] += 1
        n = max(len(gw_picks), 1)
        differentials["template"] = [
            {"player": pname(pid), "team": pteam(pid), "count": c,
             "pct": round(100 * c / n)}
            for pid, c in owner_count.most_common(10)
        ]
        differentials["unique"] = [
            {"player": pname(pid), "team": pteam(pid), "owner": owners[pid][0],
             "points": live_points.get(current_gw, {}).get(pid)}
            for pid, c in owner_count.items() if c == 1
        ][:12]
        differentials["captaincy"] = [
            {"player": pname(pid), "team": pteam(pid), "count": c,
             "pct": round(100 * c / n),
             "points": live_points.get(current_gw, {}).get(pid)}
            for pid, c in cap_count.most_common(6)
        ]

    # ----- cup
    cup = build_cup(client, league_id, managers, config, events)

    # ----- prize pot
    prizes = build_prizes(config, months, managers, cup)

    # ----- auto commentary
    commentary = auto_commentary(latest_month, managers, boot, hall, differentials, basis)

    # ----- hand-written commentary (dropped in by the monthly task)
    written = {}
    wpath = ROOT / "docs" / "commentary.json"
    if wpath.exists():
        try:
            written = json.loads(wpath.read_text())
        except json.JSONDecodeError:
            written = {}

    top5 = sorted(managers, key=lambda m: -m["total"])[:5] if current_gw else []

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": config.get("season_label", ""),
        "basis": basis,
        "league": {
            "id": league_id,
            "name": league_meta.get("name", f"League {league_id}"),
            "size": len(managers),
            "join_code": config.get("join_code"),
        },
        "current_gw": current_gw,
        "next_deadline": next(
            (to_uk(parse_dt(e["deadline_time"])).isoformat()
             for e in events if not e.get("finished") and e.get("deadline_time")), None),
        "events": [
            {"id": e["id"], "name": e["name"],
             "deadline": to_uk(parse_dt(e["deadline_time"])).isoformat() if e.get("deadline_time") else None,
             "finished": bool(e.get("finished")), "checked": bool(e.get("data_checked")),
             "avg": e.get("average_entry_score"), "highest": e.get("highest_score")}
            for e in events
        ],
        "months": months,
        "managers": managers,
        "top5": [{"entry": m["entry"], "manager": m["manager"], "team": m["team"],
                  "total": m["total"], "rank": m["rank"]} for m in top5],
        "hall_of_shame": hall,
        "player_tables": player_tables,
        "differentials": differentials,
        "cup": cup,
        "prizes": prizes,
        "commentary": {"auto": commentary, "written": written},
    }


# ------------------------------------------------------------------ cup

def build_cup(client, league_id, managers, config, events) -> dict:
    """FPL creates mini-league cups automatically once the league qualifies.
    Try the league-level endpoint first, then reconstruct the bracket from each
    manager's own cup feed, which is the more dependable route."""
    out = {
        "status": "not_started",
        "message": "FPL creates the mini-league cup automatically. Fixtures appear "
                   "here as soon as the draw is made (usually around GW34).",
        "start_event": config.get("cup_start_event"),
        "rounds": [],
        "source": None,
    }

    # League position for each entry, so we can spot a giant-killing.
    seeds = {m["entry"]: (m.get("rank") or 0) for m in managers}
    finished = {e["id"] for e in events if e.get("finished") and e.get("data_checked")}

    league_cup = client.league_cup(league_id)
    if league_cup:
        out["source"] = league_cup.get("_endpoint")
        cl = league_cup.get("cup_league") or {}
        if cl.get("start_event"):
            out["start_event"] = cl["start_event"]
        matches = league_cup.get("matches") or []
        if matches:
            out["status"] = "active"
            out["rounds"] = group_cup_matches(matches, seeds, finished)
            out["message"] = ""
            return out

    # Fallback: assemble from per-entry cup feeds.
    seen: dict[int, dict] = {}
    status_note = None
    for m in managers:
        data = client.entry_cup(m["entry"])
        if not data:
            continue
        cs = data.get("cup_status") or {}
        if cs.get("qualification_state") and not status_note:
            status_note = cs
        for match in data.get("cup_matches", []) or []:
            seen[match.get("id", id(match))] = match
    if seen:
        out["status"] = "active"
        out["source"] = "entry/{id}/cup/"
        out["rounds"] = group_cup_matches(list(seen.values()), seeds, finished)
        out["message"] = ""
    elif status_note:
        out["qualification"] = status_note
    return out


def group_cup_matches(matches: list[dict], seeds: dict[int, int] | None = None,
                      finished: set[int] | None = None) -> list[dict]:
    """Group cup ties into rounds and work out the story of each round: the
    thrashing, the nail-biter, and any manager who knocked out someone above
    them in the league. This is what the weekly cup bulletin is written from."""
    seeds = seeds or {}
    finished = finished or set()
    by_event: dict[int, list[dict]] = defaultdict(list)
    for mt in matches:
        hp, ap = mt.get("entry_1_points"), mt.get("entry_2_points")
        by_event[mt.get("event") or 0].append({
            "id": mt.get("id"),
            "event": mt.get("event"),
            "round": mt.get("knockout_name") or "",
            "home": {"entry": mt.get("entry_1_entry"), "team": mt.get("entry_1_name"),
                     "manager": mt.get("entry_1_player_name"), "points": hp,
                     "won": bool(mt.get("entry_1_win")),
                     "seed": seeds.get(mt.get("entry_1_entry"))},
            "away": {"entry": mt.get("entry_2_entry"), "team": mt.get("entry_2_name"),
                     "manager": mt.get("entry_2_player_name"), "points": ap,
                     "won": bool(mt.get("entry_2_win")),
                     "seed": seeds.get(mt.get("entry_2_entry"))},
            "winner": mt.get("winner"),
            "margin": abs(hp - ap) if isinstance(hp, int) and isinstance(ap, int) else None,
            "tiebreak": mt.get("tiebreak"),
            "bye": bool(mt.get("is_bye")),
        })

    rounds = []
    for ev in sorted(by_event):
        ms = by_event[ev]
        label = next((m["round"] for m in ms if m["round"]), f"Gameweek {ev}")
        played = [m for m in ms if m["margin"] is not None and not m["bye"]]
        complete = bool(played) and ev in finished

        upsets = []
        for m in played:
            win = m["home"] if m["winner"] == m["home"]["entry"] else m["away"]
            lose = m["away"] if m["winner"] == m["home"]["entry"] else m["home"]
            # A lower league position number is better, so a bigger seed wins = upset.
            if win.get("seed") and lose.get("seed") and win["seed"] > lose["seed"]:
                upsets.append({"id": m["id"],
                               "winner": win["manager"], "winner_seed": win["seed"],
                               "loser": lose["manager"], "loser_seed": lose["seed"],
                               "score": f"{win['points']}-{lose['points']}"})
        upsets.sort(key=lambda u: u["winner_seed"] - u["loser_seed"], reverse=True)

        rounds.append({
            "event": ev, "label": label, "matches": ms, "complete": complete,
            "survivors": [m["home"]["manager"] if m["winner"] == m["home"]["entry"]
                          else m["away"]["manager"] for m in played],
            "biggest_win": max(played, key=lambda m: m["margin"], default=None),
            "closest": min(played, key=lambda m: m["margin"], default=None),
            "upsets": upsets[:3],
        })
    return rounds


# --------------------------------------------------------------- prize pot

def build_prizes(config, months, managers, cup) -> dict:
    p = config.get("prize_pot", {}) or {}
    cur = p.get("currency", "£")
    season = p.get("season", {}) or {}
    motm_val = p.get("manager_of_the_month", 0)

    month_awards = []
    for mo in months:
        if not mo.get("complete") or not mo.get("winner"):
            continue
        month_awards.append({
            "month": mo["label"], "short": mo["short"],
            "manager": mo["winner"]["manager"], "team": mo["winner"]["team"],
            "entry": mo["winner"]["entry"],
            "points": mo["winner"][config.get("motm_basis", "net")],
            "amount": motm_val,
        })

    won: dict[str, float] = defaultdict(float)
    for a in month_awards:
        won[a["manager"]] += a["amount"]

    n_months = len([m for m in months if m["gws"]])
    committed = motm_val * n_months + sum(season.values()) + \
        p.get("cup_winner", 0) + p.get("cup_runner_up", 0)
    collected = p.get("entry_fee", 0) * max(len(managers), 1)

    return {
        "currency": cur,
        "entry_fee": p.get("entry_fee", 0),
        "collected": collected,
        "committed": committed,
        "unallocated": collected - committed,
        "motm_value": motm_val,
        "cup_winner": p.get("cup_winner", 0),
        "cup_runner_up": p.get("cup_runner_up", 0),
        "season": season,
        "months_total": n_months,
        "month_awards": month_awards,
        "running_totals": sorted(
            [{"manager": k, "amount": v} for k, v in won.items()],
            key=lambda r: -r["amount"]),
        "configured": committed > 0 or collected > 0,
    }


# ------------------------------------------------------- auto commentary

def auto_commentary(month, managers, boot, hall, diffs, basis) -> dict:
    """Stats-driven talking points. Deliberately factual with a light touch -
    the genuinely funny prose is layered on top in commentary.json."""
    if not month or not month.get("table"):
        return {"available": False,
                "headline": "Season not started - nothing to report yet.",
                "bullets": []}

    t = month["table"]
    win = t[0]
    runner = t[1] if len(t) > 1 else None
    bottom = t[-1]
    stats = month.get("stats", {})
    bullets = []

    margin = win[basis] - runner[basis] if runner else 0
    if month.get("tied"):
        tied = [r["manager"] for r in t if r["rank"] == 1]
        bullets.append(f"Dead heat at the top: {' and '.join(tied)} both finished on "
                       f"{win[basis]} points.")
    elif margin <= 3:
        bullets.append(f"{win['manager']} edged it by {margin} point"
                       f"{'s' if margin != 1 else ''} - {runner['manager']} will be "
                       f"replaying that in their head for a while.")
    else:
        bullets.append(f"{win['manager']} took the month by {margin} points. "
                       f"Comfortable.")

    spread = win[basis] - bottom[basis]
    bullets.append(f"{spread} points separated first from last "
                   f"({win['manager']} {win[basis]}, {bottom['manager']} {bottom[basis]}), "
                   f"with the league averaging {stats.get('avg')}.")

    if stats.get("total_bench"):
        worst_bench = max(t, key=lambda r: r["bench"])
        bullets.append(f"The league left {stats['total_bench']} points on the bench this "
                       f"month. {worst_bench['manager']} accounted for "
                       f"{worst_bench['bench']} of them.")

    if stats.get("total_hits"):
        worst_hit = max(t, key=lambda r: r["hits"])
        if worst_hit["hits"]:
            bullets.append(f"{stats['total_hits']} points went up in smoke on transfer "
                           f"hits. {worst_hit['manager']} took {worst_hit['hits']} "
                           f"of that on the chin.")
    else:
        bullets.append("Not a single transfer hit taken all month. Either great "
                       "discipline or nobody was paying attention.")

    chippers = [(r["manager"], c) for r in t for c in r.get("chips", [])]
    if chippers:
        pretty = {"3xc": "Triple Captain", "bboost": "Bench Boost",
                  "freehit": "Free Hit", "wildcard": "Wildcard"}
        bits = [f"{m} ({pretty.get(c, c)})" for m, c in chippers[:5]]
        bullets.append("Chips played: " + ", ".join(bits) + ".")

    flops = [f for f in hall.get("captain_flops", []) if f["gw"] in month["gws"]]
    if flops:
        f = flops[0]
        bullets.append(f"Captain of the month award goes to {f['manager']}, who handed "
                       f"the armband to {f['player']} for a {f['points']}-point return "
                       f"in GW{f['gw']}.")

    if diffs.get("captaincy"):
        c = diffs["captaincy"][0]
        bullets.append(f"Most-captained in GW{diffs['gw']}: {c['player']} "
                       f"({c['pct']}% of the league).")

    return {
        "available": True,
        "month": month["label"],
        "headline": f"{win['manager']} is {month['short']}'s Manager of the Month "
                    f"with {win[basis]} points.",
        "bullets": bullets,
        "podium": [{"pos": ordinal(r["rank"]), "manager": r["manager"],
                    "team": r["team"], "points": r[basis]} for r in t[:3]],
    }


# ------------------------------------------------------------------- entry

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data.json"))
    ap.add_argument("--offline", help="directory of canned API responses (testing)")
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text())
    client = FPLClient(offline_fixture_dir=args.offline)
    data = build(config, client)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))

    print(f"league   : {data['league']['name']} ({data['league']['size']} managers)")
    print(f"gameweek : {data['current_gw']}")
    print(f"months   : {', '.join(m['label'] + ('*' if m['complete'] else '') for m in data['months'])}")
    print(f"requests : {client.requests_made} live, {client.cache_hits} cached")
    print(f"written  : {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
