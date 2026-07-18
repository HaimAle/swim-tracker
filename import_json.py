# -*- coding: utf-8 -*-
"""
import_json.py  —  Load a swimmers.json (or data.js) INTO swim.db.

Use this once to make swim.db match your repaired swimmers.json. After that the
daily scraper only appends new data to swim.db. By default this REPLACES the
current DB contents with the file (so the DB == the JSON); use --append to add
without clearing.

Usage:
    python import_json.py                 # reads swimmers.json, replaces swim.db contents
    python import_json.py data.js         # or import from a data.js
    python import_json.py swimmers.json --append   # merge instead of replace

Tip: back up first ->  copy swim.db swim.db.backup
"""
import json, sys, os
import db as DB

def load(path):
    t = open(path, encoding="utf-8").read()
    if "window.SWIM_DATA" in t:
        t = t[t.index("{"): t.rstrip().rstrip(";").rindex("}") + 1]
    return json.loads(t)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    append = "--append" in sys.argv
    path = args[0] if args else "swimmers.json"
    if not os.path.exists(path):
        print("File not found:", path); sys.exit(1)

    d = load(path); sw = d.get("swimmers", {})
    con = DB.connect()
    if not append:
        con.execute("DELETE FROM result"); con.execute("DELETE FROM future"); con.commit()
        print("Cleared existing DB rows (fresh import).")

    results, future = [], []
    for s in sw.values():
        key = s.get("id") or DB.swimmer_key(s.get("displayName",""), s.get("birthYear"))
        name = s.get("displayName",""); yr = s.get("birthYear")
        for r in s.get("past", []):
            if r.get("compId") is None: continue
            results.append({"swimmer_key": key, "display_name": name, "birth_year": yr,
                "club": r.get("club"), "comp_id": r.get("compId"), "competition": r.get("competition"),
                "date": r.get("date"), "event": r.get("event"), "category": r.get("category"),
                "pool_length": r.get("poolLength"), "place": r.get("place"),
                "time_str": r.get("timeStr"), "seconds": r.get("seconds"), "points": r.get("points")})
        for r in s.get("future", []):
            if r.get("compId") is None: continue
            future.append({"swimmer_key": key, "display_name": name, "birth_year": yr,
                "club": r.get("club"), "comp_id": r.get("compId"), "competition": r.get("competition"),
                "date": r.get("date"), "event": r.get("event"), "category": r.get("category"),
                "pool_length": r.get("poolLength"), "start_time": r.get("startTime"),
                "heat": r.get("heat"), "lane": r.get("lane"),
                "seed_str": r.get("seedStr"), "seed_seconds": r.get("seedSeconds")})

    if results: DB.save_results(con, results)
    if future:  DB.save_future(con, future)
    total = DB.export(con, is_seed=False, new_results=0)
    print("Imported %d past results + %d future entries for %d swimmers into swim.db."
          % (len(results), len(future), total))
    print("Re-exported swimmers.json + data.js from swim.db. Done.")

if __name__ == "__main__":
    main()
