# -*- coding: utf-8 -*-
"""
fill_future.py  —  Populate שעת התחלה / מקצה / מסלול / זמן כניסה for upcoming meets,
                   with the correct PER-EVENT date (multi-day meets run events on
                   different days, e.g. 200 free on day 2).

The upcoming competitions in your file were originally crawled as (not-yet-swum)
NS results, so they have no start-list detail. This fetches the real START LISTS
from loglig for just the upcoming competitions and rebuilds each swimmer's future
entries with heat, lane, start time, seed (entry) time — and each event's own date.

Reuses scraper.py's tested loglig parsers, so keep it in the same folder.
Needs internet + requests + beautifulsoup4:   pip install -r requirements.txt

Usage:
    python fill_future.py                 # reads swimmers.json
    python fill_future.py data.js         # or any data.js / swimmers.json

Output (original untouched):
    swimmers.future.json
    data.js   (put next to index.html and reload)
"""
import json, sys, os, re, datetime
import scraper as S

TODAY = datetime.date.today().isoformat()

def key(name, year):
    return "%s|%s" % (year, "·".join(sorted(str(name).split())))

def iso_from(s):
    """'DD/MM/YYYY HH:MM' -> 'YYYY-MM-DD' (per-event date)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    if m:
        try:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None

def hhmm(s):
    m = re.search(r"(\d{1,2}:\d{2})", s or "")
    return m.group(1) if m else ""

def load(path):
    t = open(path, encoding="utf-8").read()
    if "window.SWIM_DATA" in t:
        t = t[t.index("{"): t.rstrip().rstrip(";").rindex("}") + 1]
    return json.loads(t)

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "swimmers.json"
    if not os.path.exists(path):
        print("File not found:", path); sys.exit(1)
    d = load(path)
    sw = d.get("swimmers", {})

    # upcoming competitions = compIds that appear on any entry dated today or later
    comps, comp_name = set(), {}
    for s in sw.values():
        for r in s.get("future", []) + s.get("past", []):
            cid = r.get("compId")
            if cid is None:
                continue
            comp_name.setdefault(cid, r.get("competition", ""))
            if r.get("date") and r["date"] >= TODAY:
                comps.add(cid)
    print("%d upcoming competitions to refresh from start lists ..." % len(comps))

    def ensure(name, year, club):
        k = key(name, year)
        s = sw.setdefault(k, {"id": k, "displayName": name, "birthYear": year,
                              "clubs": [], "past": [], "future": []})
        if club and club not in s["clubs"]:
            s["clubs"].append(club)
        return s

    # drop stale placeholder future entries for these competitions
    for s in sw.values():
        s["future"] = [r for r in s.get("future", []) if r.get("compId") not in comps]

    added = 0
    for i, cid in enumerate(sorted(comps), 1):
        name = comp_name.get(cid, "comp %s" % cid)
        pool = S.detect_course(cid, name)
        evd = S.get_events(cid)
        for e in evd["events"]:
            if S.is_relay(e["event"]):
                continue
            edate = iso_from(e.get("startTime")) or evd["date"] or TODAY   # PER-EVENT date
            etime = hhmm(e.get("startTime"))
            for r in S.parse_startlist(e["eventId"]):
                s = ensure(r["fullName"], r["year"], r["club"])
                s["future"].append({
                    "date": edate, "competition": name, "compId": cid,
                    "event": e["event"], "category": e["category"], "poolLength": pool,
                    "club": r["club"], "startTime": r["startTime"] or etime,
                    "heat": r["heat"], "lane": r["lane"],
                    "seedStr": r["seedStr"], "seedSeconds": r["seedSeconds"]})
                added += 1
        if i % 5 == 0:
            print("  %d/%d ..." % (i, len(comps)))

    for s in sw.values():
        s["future"].sort(key=lambda r: (r.get("date") or "", r.get("startTime") or ""))

    json.dump(d, open("swimmers.future.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open("data.js", "w", encoding="utf-8") as f:
        f.write("// Future start-lists filled by fill_future.py\nwindow.SWIM_DATA = ")
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print("Added %d start-list entries across %d competitions." % (added, len(comps)))
    print("Wrote swimmers.future.json + data.js. Put data.js next to index.html and reload.")

if __name__ == "__main__":
    main()
