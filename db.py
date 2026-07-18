# -*- coding: utf-8 -*-
"""
db.py  —  Persistent SQLite backend for the swimmer tracker.

Why a DB: so we never re-scrape everything. Past results are stored once and
kept forever; each crawl only *adds* rows it hasn't seen (upsert on a natural
key). The static app reads an exported data.js, which is regenerated from the DB.

Tables
------
result  : finished swims (one row per swimmer × event × competition)
future  : start-list / registration entries for upcoming competitions

Natural key (dedupe): (comp_id, event, category, swimmer_key)
 -> re-running a crawl updates a row in place instead of duplicating it.

Course / pool length is stored per row as pool_length (25 or 50).
"""
import os, sqlite3, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "swim.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS result (
  swimmer_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  birth_year  INTEGER,
  club        TEXT,
  comp_id     INTEGER NOT NULL,
  competition TEXT,
  date        TEXT,
  event       TEXT NOT NULL,
  category    TEXT NOT NULL,
  pool_length INTEGER,           -- 25 or 50
  place       TEXT,
  time_str    TEXT,
  seconds     REAL,
  points      INTEGER,
  PRIMARY KEY (comp_id, event, category, swimmer_key)
);
CREATE INDEX IF NOT EXISTS ix_result_swimmer ON result(swimmer_key);

CREATE TABLE IF NOT EXISTS future (
  swimmer_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  birth_year  INTEGER,
  club        TEXT,
  comp_id     INTEGER NOT NULL,
  competition TEXT,
  date        TEXT,
  event       TEXT NOT NULL,
  category    TEXT NOT NULL,
  pool_length INTEGER,
  start_time  TEXT,
  heat        TEXT,
  lane        TEXT,
  seed_str    TEXT,
  seed_seconds REAL,
  PRIMARY KEY (comp_id, event, category, swimmer_key)
);
CREATE INDEX IF NOT EXISTS ix_future_swimmer ON future(swimmer_key);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con

def count_results(con):
    return con.execute("SELECT COUNT(*) FROM result").fetchone()[0]

def has_comp(con, comp_id):
    return con.execute("SELECT 1 FROM result WHERE comp_id=? LIMIT 1", (comp_id,)).fetchone() is not None

def comp_has_placeholder(con, comp_id):
    """True if this competition still holds seed/placeholder rows (empty 'place'),
    i.e. it was stored from a start list before the meet actually happened."""
    return con.execute("SELECT 1 FROM result WHERE comp_id=? AND (place='' OR place IS NULL) LIMIT 1",
                       (comp_id,)).fetchone() is not None

def delete_results_for_comp(con, comp_id):
    con.execute("DELETE FROM result WHERE comp_id=?", (comp_id,)); con.commit()

def swimmer_key(full_name, year):
    toks = sorted(str(full_name).split())
    return f"{year}|" + "·".join(toks)

def save_results(con, rows):
    """rows: list of dicts. INSERT OR REPLACE on the natural key."""
    con.executemany("""
      INSERT OR REPLACE INTO result
      (swimmer_key,display_name,birth_year,club,comp_id,competition,date,event,
       category,pool_length,place,time_str,seconds,points)
      VALUES (:swimmer_key,:display_name,:birth_year,:club,:comp_id,:competition,
       :date,:event,:category,:pool_length,:place,:time_str,:seconds,:points)
    """, rows)
    con.commit()

def save_future(con, rows):
    con.executemany("""
      INSERT OR REPLACE INTO future
      (swimmer_key,display_name,birth_year,club,comp_id,competition,date,event,
       category,pool_length,start_time,heat,lane,seed_str,seed_seconds)
      VALUES (:swimmer_key,:display_name,:birth_year,:club,:comp_id,:competition,
       :date,:event,:category,:pool_length,:start_time,:heat,:lane,:seed_str,:seed_seconds)
    """, rows)
    con.commit()

def clear_future_for_comp(con, comp_id):
    """Once a competition has results, drop its stale start-list rows."""
    con.execute("DELETE FROM future WHERE comp_id=?", (comp_id,))
    con.commit()

def set_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
    con.commit()

def build_dataset(con, is_seed=False):
    """Group DB rows into the {swimmers:{...}} structure the app expects."""
    swimmers = {}
    def ensure(key, name, year, club):
        s = swimmers.setdefault(key, {"id": key, "displayName": name,
                                      "birthYear": year, "clubs": [], "past": [], "future": []})
        if club and club not in s["clubs"]:
            s["clubs"].append(club)
        return s

    for d in con.execute("SELECT * FROM result ORDER BY date, event").fetchall():
        s = ensure(d["swimmer_key"], d["display_name"], d["birth_year"], d["club"])
        s["past"].append({"date": d["date"], "competition": d["competition"], "compId": d["comp_id"],
            "event": d["event"], "category": d["category"], "poolLength": d["pool_length"],
            "club": d["club"], "timeStr": d["time_str"], "seconds": d["seconds"],
            "place": d["place"], "points": d["points"]})

    for d in con.execute("SELECT * FROM future ORDER BY date, start_time").fetchall():
        s = ensure(d["swimmer_key"], d["display_name"], d["birth_year"], d["club"])
        s["future"].append({"date": d["date"], "competition": d["competition"], "compId": d["comp_id"],
            "event": d["event"], "category": d["category"], "poolLength": d["pool_length"],
            "club": d["club"], "startTime": d["start_time"], "heat": d["heat"], "lane": d["lane"],
            "seedStr": d["seed_str"], "seedSeconds": d["seed_seconds"]})

    return {"generated": datetime.date.today().isoformat(),
            "source": "isr.org.il  →  loglig.com  (SQLite backend)",
            "isSeedSample": is_seed, "swimmerCount": len(swimmers), "swimmers": swimmers}

def export(con, is_seed=False, new_results=0, new_comps=None):
    """Write swimmers.json + data.js from the current DB state.
    Records lastChecked (every run) and newThisRun; 'generated' advances only
    when new results were actually added."""
    data = build_dataset(con, is_seed=is_seed)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.date.today().isoformat()
    row = con.execute("SELECT v FROM meta WHERE k='last_data_update'").fetchone()
    prev = row[0] if row else None
    last_update = today if (new_results > 0 or not prev) else prev
    set_meta(con, "last_data_update", last_update)
    set_meta(con, "last_checked", now)
    data["generated"] = last_update
    data["lastChecked"] = now
    data["newThisRun"] = int(new_results)
    data["newComps"] = (new_comps or [])[:30]
    json.dump(data, open(os.path.join(HERE, "swimmers.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, "data.js"), "w", encoding="utf-8") as f:
        f.write("// Auto-generated from swim.db\nwindow.SWIM_DATA = ")
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    return data["swimmerCount"]
