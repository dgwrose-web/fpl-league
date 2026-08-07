#!/usr/bin/env python3
"""Generate schema-accurate fake FPL API responses so the pipeline can be tested
without network access. Mirrors the real endpoint shapes exactly.

Usage: python3 scripts/make_mock.py --out /tmp/mock --gws 9
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEAGUE_ID = 625223
LEAGUE_NAME = "Demo League — SIMULATED DATA"

# Entirely fictional. This file exists only to exercise the pipeline offline;
# none of these names or scores relate to the real league.
MANAGERS = [
    ("Alex Trundle", "Trundle Down Under"), ("Bea Cottrell", "Cottrell Panic"),
    ("Cliff Mandry", "Mandry Grease"), ("Dot Prewitt", "Prewitt and Weep"),
    ("Errol Sankey", "Sankey Panky"), ("Fay Brindle", "Brindle Bells"),
    ("Gus Lattimer", "Lattimer Days"), ("Hana Vosper", "Vosper Cadets"),
    ("Ivo Blanchflower", "Blanch of the Dead"), ("Jo Merrick", "Merrick Go Round"),
    ("Kip Danvers", "Danvers Mouse"), ("Lena Fairhurst", "Fairhurst Enough"),
    ("Mo Quiller", "Quiller Instinct"), ("Nell Ashby", "Ashby Ashby Ashby"),
    ("Otto Renshaw", "Renshaw Redemption"), ("Pia Goodliffe", "Goodliffe if You Can"),
    ("Quinn Farrow", "Farrow the Leader"), ("Rex Halloway", "Halloway to Hell"),
    ("Suki Tebbit", "Tebbit or Not Tebbit"), ("Tarquin Nye", "Nye Regrets"),
]

# Real-shape 2026/27 calendar. Weekends chosen so the month split lands on
# August = GW1-2, September = GW3-5, October = GW6-9 (GW9 spilling into November).
WEEKENDS = [
    "2026-08-22", "2026-08-29",                              # Aug: GW1-2
    "2026-09-05", "2026-09-12", "2026-09-19",                # Sep: GW3-5
    "2026-10-03", "2026-10-17", "2026-10-24", "2026-10-31",  # Oct: GW6-9
    "2026-11-07", "2026-11-21", "2026-11-28",
    "2026-12-05", "2026-12-12", "2026-12-19", "2026-12-26", "2026-12-29",
    "2027-01-02", "2027-01-16", "2027-01-23", "2027-01-30",
    "2027-02-06", "2027-02-13", "2027-02-20", "2027-02-27",
    "2027-03-06", "2027-03-13", "2027-03-20",
    "2027-04-03", "2027-04-10", "2027-04-17", "2027-04-24",
    "2027-05-01", "2027-05-08", "2027-05-15", "2027-05-22", "2027-05-26", "2027-05-30",
]

TEAMS = ["ARS", "AVL", "BOU", "BRE", "BHA", "BUR", "CHE", "CRY", "EVE", "FUL",
         "LEE", "LIV", "MCI", "MUN", "NEW", "NFO", "SUN", "TOT", "WHU", "WOL"]

PLAYERS = [
    ("Haaland", 13), ("Salah", 12), ("Saka", 1), ("Palmer", 7), ("Isak", 12),
    ("Watkins", 2), ("Mbeumo", 14), ("Bruno F.", 14), ("Gordon", 15), ("Rice", 1),
    ("Gvardiol", 13), ("Trippier", 15), ("Raya", 1), ("Sánchez", 7), ("Wood", 16),
    ("Cunha", 14), ("Rogers", 2), ("Semenyo", 3), ("Wirtz", 12), ("Sesko", 14),
    ("Virgil", 12), ("Gabriel", 1), ("Timber", 1), ("Muñoz", 8), ("Pickford", 9),
    ("Foden", 13), ("Bowen", 19), ("Kudus", 18), ("Enzo", 7), ("Anderson", 16),
]


def iso(d: str, h: int, m: int = 0) -> str:
    return datetime.fromisoformat(d).replace(hour=h, minute=m, tzinfo=timezone.utc) \
        .isoformat().replace("+00:00", "Z")


def build(out: Path, finished_gws: int, seed: int, with_cup: bool) -> None:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)

    def write(path: str, obj) -> None:
        name = (path.strip("/").replace("/", "_").replace("?", "_")
                .replace("&", "_").replace("=", "-") + ".json")
        (out / name).write_text(json.dumps(obj))

    # ---------------- bootstrap-static
    events, fixtures = [], []
    fid = 1
    for i, wknd in enumerate(WEEKENDS, start=1):
        sat = datetime.fromisoformat(wknd)
        deadline = sat - timedelta(days=1)
        deadline = deadline.replace(hour=17, minute=30, tzinfo=timezone.utc)
        fin = i <= finished_gws
        events.append({
            "id": i, "name": f"Gameweek {i}",
            "deadline_time": deadline.isoformat().replace("+00:00", "Z"),
            "average_entry_score": rng.randint(38, 68) if fin else 0,
            "finished": fin, "data_checked": fin,
            "highest_scoring_entry": None,
            "highest_score": rng.randint(95, 135) if fin else None,
            "is_previous": i == finished_gws, "is_current": i == finished_gws,
            "is_next": i == finished_gws + 1,
            "chip_plays": [], "most_selected": 1, "most_transferred_in": 2,
            "top_element": 1, "top_element_info": None, "transfers_made": 0,
            "most_captained": 1, "most_vice_captained": 2,
        })
        # 10 fixtures: Sat x6, Sun x3, Mon x1 - so a GW straddling a month end
        # still has its majority on the Saturday.
        for n in range(10):
            if n < 6:
                day, hour = sat, [12, 15, 15, 15, 15, 17][n]
            elif n < 9:
                day, hour = sat + timedelta(days=1), [14, 14, 16][n - 6]
            else:
                day, hour = sat + timedelta(days=2), 20
            fixtures.append({
                "id": fid, "event": i,
                "kickoff_time": day.replace(hour=hour, tzinfo=timezone.utc)
                                   .isoformat().replace("+00:00", "Z"),
                "finished": fin, "team_h": (n * 2) % 20 + 1, "team_a": (n * 2 + 1) % 20 + 1,
                "team_h_score": rng.randint(0, 3) if fin else None,
                "team_a_score": rng.randint(0, 3) if fin else None,
            })
            fid += 1

    elements = [{"id": i, "web_name": nm, "team": tm, "element_type": (i % 4) + 1,
                 "now_cost": rng.randint(40, 150), "total_points": rng.randint(0, 200),
                 "selected_by_percent": f"{rng.uniform(0.1, 60):.1f}"}
                for i, (nm, tm) in enumerate(PLAYERS, start=1)]

    write("bootstrap-static/", {
        "events": events,
        "elements": elements,
        "teams": [{"id": i, "name": t, "short_name": t} for i, t in enumerate(TEAMS, start=1)],
        "element_types": [{"id": i, "singular_name_short": s}
                          for i, s in enumerate(["GKP", "DEF", "MID", "FWD"], start=1)],
        "total_players": 11_000_000,
    })
    write("fixtures/", fixtures)

    # ---------------- live element points per gameweek
    live_pts: dict[int, dict[int, int]] = {}
    for gw in range(1, finished_gws + 1):
        pts = {}
        for e in elements:
            r = rng.random()
            pts[e["id"]] = 0 if r < 0.18 else (2 if r < 0.55 else
                                               (rng.randint(5, 9) if r < 0.9 else rng.randint(10, 21)))
        live_pts[gw] = pts
        write(f"event/{gw}/live/", {
            "elements": [{"id": k, "stats": {"total_points": v, "minutes": 90,
                                             "goals_scored": 0, "assists": 0, "bonus": 0},
                          "explain": []} for k, v in pts.items()]
        })

    # ---------------- managers
    entries = []
    histories: dict[int, list[dict]] = {}
    squads: dict[int, list[int]] = {}
    for idx, (person, team) in enumerate(MANAGERS):
        eid = 100000 + idx * 137
        entries.append({"entry": eid, "person": person, "team": team})
        squads[eid] = rng.sample([e["id"] for e in elements], 15)

    for e in entries:
        eid, running, rows = e["entry"], 0, []
        for gw in range(1, finished_gws + 1):
            base = rng.randint(28, 95)
            transfers = rng.choice([0, 0, 1, 1, 1, 2, 3])
            free = 1
            cost = max(0, transfers - free) * 4 if rng.random() < 0.35 else 0
            running += base - cost
            rows.append({
                "event": gw, "points": base, "total_points": running,
                "rank": rng.randint(1, 11_000_000), "rank_sort": rng.randint(1, 11_000_000),
                "overall_rank": rng.randint(1, 11_000_000),
                "bank": rng.randint(0, 40), "value": 1000 + gw * rng.randint(-2, 6),
                "event_transfers": transfers, "event_transfers_cost": cost,
                "points_on_bench": rng.choice([0, 1, 2, 3, 5, 8, 12, 17]),
            })
        histories[eid] = rows

        chips = []
        if finished_gws >= 4:
            chips.append({"name": "wildcard", "time": iso(WEEKENDS[3], 12), "event": 4})
        if finished_gws >= 7:
            chips.append({"name": "3xc", "time": iso(WEEKENDS[6], 12), "event": 7})
        write(f"entry/{eid}/history/", {"current": rows, "past": [], "chips": chips})
        write(f"entry/{eid}/", {
            "id": eid, "name": e["team"],
            "player_first_name": e["person"].split()[0],
            "player_last_name": e["person"].split()[-1],
            "summary_overall_points": rows[-1]["total_points"] if rows else 0,
        })

        for gw in range(1, finished_gws + 1):
            squad = squads[eid]
            cap = squad[rng.randint(0, 4)]
            vice = squad[(squad.index(cap) + 1) % 11]
            picks = []
            for pos, pid in enumerate(squad, start=1):
                picks.append({
                    "element": pid, "position": pos,
                    "multiplier": 0 if pos > 11 else (2 if pid == cap else 1),
                    "is_captain": pid == cap, "is_vice_captain": pid == vice,
                })
            row = histories[eid][gw - 1]
            write(f"entry/{eid}/event/{gw}/picks/", {
                "active_chip": None, "automatic_subs": [], "picks": picks,
                "entry_history": row,
            })

    # ---------------- league standings
    table = []
    for e in entries:
        rows = histories[e["entry"]]
        table.append({
            "id": rng.randint(1, 10**7), "entry": e["entry"],
            "player_name": e["person"], "entry_name": e["team"],
            "event_total": rows[-1]["points"] if rows else 0,
            "total": rows[-1]["total_points"] if rows else 0,
            "rank": 0, "last_rank": 0, "rank_sort": 0,
        })
    table.sort(key=lambda r: -r["total"])
    for i, r in enumerate(table, start=1):
        r["rank"] = r["rank_sort"] = i
        r["last_rank"] = max(1, i + rng.randint(-2, 2))

    write(f"leagues-classic/{LEAGUE_ID}/standings/?page_standings=1", {
        "league": {"id": LEAGUE_ID, "name": LEAGUE_NAME, "created": "2026-08-06T09:00:00Z",
                   "closed": False, "league_type": "x", "scoring": "c",
                   "admin_entry": entries[3]["entry"], "start_event": 1},
        "new_entries": {"has_next": False, "page": 1, "results": []},
        "standings": {"has_next": False, "page": 1,
                      "results": table if finished_gws else []},
    })

    # ---------------- cup (only exists late in the season)
    if with_cup and finished_gws >= 34:
        matches, mid = [], 1
        alive = [e["entry"] for e in table[:16]]
        names = {e["entry"]: (e["team"], e["person"]) for e in entries}
        for rnd, gw in enumerate([34, 35, 36, 37], start=1):
            if gw > finished_gws or len(alive) < 2:
                break
            label = {16: "Round of 16", 8: "Quarter-Final",
                     4: "Semi-Final", 2: "Final"}.get(len(alive), f"Round {rnd}")
            nxt = []
            for i in range(0, len(alive) - 1, 2):
                a, b = alive[i], alive[i + 1]
                pa = histories[a][gw - 1]["points"]
                pb = histories[b][gw - 1]["points"]
                if pa == pb:
                    pa += 1
                w = a if pa > pb else b
                nxt.append(w)
                matches.append({
                    "id": mid, "event": gw, "knockout_name": label,
                    "entry_1_entry": a, "entry_1_name": names[a][0],
                    "entry_1_player_name": names[a][1], "entry_1_points": pa,
                    "entry_1_win": int(w == a), "entry_1_draw": 0, "entry_1_loss": int(w != a),
                    "entry_2_entry": b, "entry_2_name": names[b][0],
                    "entry_2_player_name": names[b][1], "entry_2_points": pb,
                    "entry_2_win": int(w == b), "entry_2_draw": 0, "entry_2_loss": int(w != b),
                    "is_knockout": True, "winner": w, "seed_value": None,
                    "tiebreak": None, "is_bye": False,
                })
                mid += 1
            alive = nxt
        write(f"league/{LEAGUE_ID}/cup/?page_new_entries=1&page_standings=1", {
            "cup_league": {"id": 99, "name": f"{LEAGUE_NAME} Cup", "start_event": 34},
            "matches": matches,
            "status": {"qualification_event": 33, "qualification_state": "QUALIFIED"},
        })

    print(f"wrote {len(list(out.glob('*.json')))} mock files to {out} "
          f"({finished_gws} finished GWs, cup={'yes' if with_cup and finished_gws >= 34 else 'no'})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gws", type=int, default=9)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cup", action="store_true")
    a = ap.parse_args()
    build(Path(a.out), a.gws, a.seed, a.cup)
