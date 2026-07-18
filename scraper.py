# -*- coding: utf-8 -*-
"""
scraper.py  —  Israeli Swimming (איגוד השחייה) results crawler.

WHAT IT DOES
------------
The isr.org.il site is only an index; every competition links out to loglig.com,
which holds the real data behind clean, server-rendered endpoints:

    isr.org.il/comp.asp?compID=<isr>              -> contains the loglig link
    loglig.com:2053/LeagueTable/AthleticsDisciplines/<comp>   -> list of events
    loglig.com:2053/LeagueTable/AthleticsDisciplineResults/<eventId>   -> past results
    loglig.com:2053/LeagueTable/StartList/<eventId>                    -> future start list

This crawler walks isr.org.il competition IDs, resolves each to its loglig
competition, pulls every event's results (past) or start list (future), and
builds a swimmer-indexed dataset keyed by (normalized name + birth year).
Output: swimmers.json (full data) and data.js (what index.html loads).

USAGE
-----
    pip install -r requirements.txt

    # Daily incremental refresh (default): recent + upcoming competitions only.
    python scraper.py

    # One-time historical backfill of an isr compID range (all seasons):
    python scraper.py --backfill 12000 17000

    # Refresh a specific competition list:
    python scraper.py --isr-ids 16689 16690 16730

State is cached in cache/competitions_index.json so daily runs stay fast and
only re-pull competitions that are recent, upcoming, or newly discovered.

NOTE ON ROBUSTNESS: loglig's HTML is stable but not a contract. Parsing is
defensive — a row/endpoint that doesn't match is skipped and logged, never
crashes the run. Verify counts in the run summary after each execution.
"""
import argparse, json, os, re, sys, time, datetime
import requests
from bs4 import BeautifulSoup
import db as DB

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
ISR_BASE = "https://isr.org.il"
LOGLIG = "https://loglig.com:2053"
HEADERS = {"User-Agent": "Mozilla/5.0 (swim-tracker; personal use)"}
TIMEOUT = 30
SLEEP = 0.4  # be polite between requests

# ---- Pool length (course) ----
# The source rarely states course explicitly, so we infer it. Israeli winter
# leagues are short course (25 m); summer national championships are long
# course (50 m). Pin any known competition here by its loglig id to override.
COURSE_OVERRIDE = {
    13315: 25, 13316: 25, 13514: 25, 13687: 25,   # winter league rounds
    15252: 50,                                      # summer championship
}
def detect_course(loglig_id, name):
    if loglig_id in COURSE_OVERRIDE:
        return COURSE_OVERRIDE[loglig_id]
    n = name or ""
    if re.search(r"50\s*(מ['׳]|מטר)", n):
        return 50
    if re.search(r"25\s*(מ['׳]|מטר)", n):
        return 25
    if "אליפות" in n and ("קיץ" in n or "summer" in n.lower()):
        return 50
    return 25  # default: winter / league short course

# ---------- low level ----------
def get(url):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                r.encoding = "utf-8"   # both sites are UTF-8; isr.org.il omits the charset header
                return r.text
            return None
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return None

def secs(t):
    t = (t or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}\.\d{2}$", t):
        return None
    m, s = t.split(":")
    return round(int(m) * 60 + float(s), 2)

def is_relay(event_name):
    return ("שליחים" in event_name) or ("X" in event_name.upper()) or ("4X" in event_name)

# ---------- isr.org.il ----------
def resolve_loglig(isr_id):
    """comp.asp?compID -> {isr, loglig, name} or None. (Name is UTF-8, from isr.)"""
    html = get(f"{ISR_BASE}/comp.asp?compID={isr_id}")
    if not html:
        return None
    m = re.search(r"AthleticsDisciplines/(\d+)", html)
    if not m:
        return None
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else f"comp {isr_id}"
    return {"isr": isr_id, "loglig": int(m.group(1)), "name": name}

def loglig_date(html):
    """Earliest DD/MM/YYYY on a loglig page -> yyyy-mm-dd. (loglig uses DD/MM/YYYY.)"""
    best = None
    for d in re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", html):
        try:
            dt = datetime.datetime.strptime(d, "%d/%m/%Y").date()
            best = dt if best is None or dt < best else best
        except ValueError:
            pass
    return best.isoformat() if best else None

# ---------- loglig ----------
def get_events(loglig_id):
    """AthleticsDisciplines page -> {'date': yyyy-mm-dd, 'events': [...]}."""
    html = get(f"{LOGLIG}/LeagueTable/AthleticsDisciplines/{loglig_id}")
    if not html:
        return {"date": None, "events": []}
    comp_date = loglig_date(html)
    soup = BeautifulSoup(html, "html.parser")
    events = []
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        # find any eventId referenced in this row
        m = None
        for a in tr.find_all("a", href=True):
            m = re.search(r"/(?:AthleticsDisciplineResults|StartList|RegisteredCompetitionAthletes)/(\d+)", a["href"])
            if m:
                break
        if not m:
            continue
        event_name = tds[1].get_text(strip=True)
        category = tds[2].get_text(strip=True)
        start = ""
        for td in tds:
            mt = re.search(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}", td.get_text(" ", strip=True))
            if mt:
                start = mt.group(0); break
        events.append({"eventId": int(m.group(1)), "event": event_name,
                       "category": category, "startTime": start})
    return {"date": comp_date, "events": events}

def parse_results(event_id):
    """AthleticsDisciplineResults -> list of result rows (individual events only)."""
    html = get(f"{LOGLIG}/LeagueTable/AthleticsDisciplineResults/{event_id}?isModal=True")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 8:
            continue
        place, full_name, year, club, heat, lane, result, points = tds[:8]
        if not re.match(r"^(19|20)\d{2}$", year):
            continue
        rows.append({
            "place": place, "fullName": full_name, "year": int(year), "club": club,
            "heat": heat, "lane": lane, "timeStr": result,
            "seconds": secs(result), "points": _int(points),
        })
    return rows

def parse_startlist(event_id):
    """StartList -> list of registered swimmers with heat/lane/seed."""
    html = get(f"{LOGLIG}/LeagueTable/StartList/{event_id}?isModal=True")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows, cur_start = [], ""
    for tr in soup.select("tr"):
        txt = tr.get_text(" ", strip=True)
        mstart = re.search(r"שעת הזנקה[:\s]*(\d{1,2}:\d{2})", txt)
        if mstart:
            cur_start = mstart.group(1)
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 6:
            continue
        heat, lane, full_name, year, club, seed = tds[:6]
        if not re.match(r"^(19|20)\d{2}$", year):
            continue
        rows.append({"heat": heat, "lane": lane, "fullName": full_name,
                     "year": int(year), "club": club, "seedStr": seed,
                     "seedSeconds": secs(seed), "startTime": cur_start})
    return rows

def _int(x):
    try:
        return int(re.sub(r"[^\d]", "", x))
    except ValueError:
        return None

# ---------- build ----------
def event_date(start, fallback):
    """Per-event day from an AthleticsDisciplines 'DD/MM/YYYY HH:MM' cell."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", start or "")
    if m:
        try:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    return fallback

def event_time(start):
    m = re.search(r"(\d{1,2}:\d{2})", start or "")
    return m.group(1) if m else ""

def crawl(isr_ids, con, log):
    """Crawl competitions and upsert rows into the DB. Returns (n_past, n_future)."""
    today = datetime.date.today()
    n_past = n_future = 0
    updated_comps = []
    for isr_id in isr_ids:
        info = resolve_loglig(isr_id); time.sleep(SLEEP)
        if not info:
            continue
        loglig = info["loglig"]
        ev_data = get_events(loglig); time.sleep(SLEEP)
        date = ev_data["date"]
        pool = detect_course(loglig, info["name"])
        is_future = bool(date) and datetime.date.fromisoformat(date) >= today
        # skip meets that finished > 14 days ago and are already stored (results won't change)
        if date and not is_future and DB.has_comp(con, loglig):
            if datetime.date.fromisoformat(date) < today - datetime.timedelta(days=14):
                continue
        log(f"  comp {isr_id} -> loglig {loglig}  {date}  [{pool}m]  {info['name'][:38]}")
        past_rows, future_rows = [], []
        today_iso = today.isoformat()
        for ev in ev_data["events"]:
            time.sleep(SLEEP)
            if is_relay(ev["event"]):
                continue
            edate = event_date(ev.get("startTime"), date)     # this event's own day
            etime = event_time(ev.get("startTime"))
            ev_future = (edate >= today_iso) if edate else is_future
            if ev_future:
                for r in parse_startlist(ev["eventId"]):
                    future_rows.append({"swimmer_key": DB.swimmer_key(r["fullName"], r["year"]),
                        "display_name": r["fullName"], "birth_year": r["year"], "club": r["club"],
                        "comp_id": loglig, "competition": info["name"], "date": edate,
                        "event": ev["event"], "category": ev["category"], "pool_length": pool,
                        "start_time": r["startTime"] or etime, "heat": r["heat"],
                        "lane": r["lane"], "seed_str": r["seedStr"], "seed_seconds": r["seedSeconds"]})
            else:
                for r in parse_results(ev["eventId"]):
                    past_rows.append({"swimmer_key": DB.swimmer_key(r["fullName"], r["year"]),
                        "display_name": r["fullName"], "birth_year": r["year"], "club": r["club"],
                        "comp_id": loglig, "competition": info["name"], "date": edate,
                        "event": ev["event"], "category": ev["category"], "pool_length": pool,
                        "place": r["place"], "time_str": r["timeStr"], "seconds": r["seconds"],
                        "points": r["points"]})
            time.sleep(SLEEP)
        was_present = DB.has_comp(con, loglig)
        if past_rows:
            DB.save_results(con, past_rows); n_past += len(past_rows)
            DB.clear_future_for_comp(con, loglig)  # it happened -> drop stale start list
            if not was_present:   # brand-new competition with results this run
                updated_comps.append({"name": info["name"], "date": date, "count": len(past_rows)})
        if future_rows:
            DB.clear_future_for_comp(con, loglig)  # replace prior start list
            DB.save_future(con, future_rows); n_future += len(future_rows)
    return n_past, n_future, updated_comps

def load_index():
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, "competitions_index.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", nargs=2, type=int, metavar=("START", "END"),
                    help="scan an isr compID range once (historical, all seasons)")
    ap.add_argument("--isr-ids", nargs="+", type=int, help="crawl specific isr compIDs")
    ap.add_argument("--recent-range", nargs=2, type=int, default=[16680, 16860],
                    help="default daily scan range of isr compIDs")
    args = ap.parse_args()
    log = lambda m: print(m, flush=True)

    if args.isr_ids:
        ids = args.isr_ids
    elif args.backfill:
        ids = list(range(args.backfill[0], args.backfill[1] + 1))
    else:
        ids = list(range(args.recent_range[0], args.recent_range[1] + 1))

    log(f"Crawling {len(ids)} candidate competition IDs into swim.db ...")
    con = DB.connect()
    before = DB.count_results(con)
    n_past, n_future, updated = crawl(ids, con, log)
    after = DB.count_results(con)
    new_results = max(0, after - before)
    DB.set_meta(con, "last_run", datetime.datetime.now().isoformat(timespec="seconds"))
    total = DB.export(con, is_seed=False, new_results=new_results, new_comps=updated)
    log(f"Done. {new_results} new results this run (checked {len(ids)} competitions).")
    log(f"swim.db now covers {total} swimmers. Exported swimmers.json + data.js")

if __name__ == "__main__":
    main()
