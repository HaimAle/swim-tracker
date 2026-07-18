# -*- coding: utf-8 -*-
"""
repair.py  —  Fix an existing swim.db that was built before the encoding/date fixes.

Two problems it repairs, WITHOUT re-crawling everything:

1. Garbled Hebrew competition names (mojibake) — the old scraper decoded the
   isr.org.il pages as Latin-1. That's losslessly reversible:
   bytes were UTF-8 → re-encode as Latin-1 → decode as UTF-8. (Done offline.)

2. Missing competition dates — the old scraper misread isr's M/D/YYYY anchors.
   We refetch each competition's date from loglig (reliable DD/MM/YYYY) and,
   while we're there, recompute the 25/50 pool length from the fixed name.

Usage:
    python repair.py              # fix names (offline) + refetch dates (network)
    python repair.py --names-only # only fix names, no network

Safe to run more than once.
"""
import argparse, re, time
import db as DB
import scraper as S

HEB = re.compile(r"[֐-׿]")

def fix_mojibake(s):
    """Reverse UTF-8-decoded-as-Latin-1. Returns s unchanged if not applicable."""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s  # correctly-stored Hebrew can't be encoded to latin-1 -> leave it
    # accept only if it actually produced Hebrew text
    return fixed if (fixed != s and HEB.search(fixed)) else s

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names-only", action="store_true", help="skip the network date refetch")
    args = ap.parse_args()
    con = DB.connect()

    # ---- 1. competition names (offline) ----
    comps = con.execute(
        "SELECT DISTINCT comp_id, competition FROM result "
        "UNION SELECT DISTINCT comp_id, competition FROM future").fetchall()
    fixed_names = 0
    for cid, name in comps:
        nn = fix_mojibake(name)
        if nn != name:
            con.execute("UPDATE result SET competition=? WHERE comp_id=?", (nn, cid))
            con.execute("UPDATE future SET competition=? WHERE comp_id=?", (nn, cid))
            fixed_names += 1
    con.commit()
    print(f"Fixed {fixed_names} garbled competition names.")

    # ---- 2. dates + pool length (network) ----
    if not args.names_only:
        cids = [r[0] for r in con.execute(
            "SELECT DISTINCT comp_id FROM result "
            "UNION SELECT DISTINCT comp_id FROM future")]
        print(f"Refetching dates for {len(cids)} competitions from loglig ...")
        fixed_dates = 0
        for i, cid in enumerate(cids, 1):
            row = (con.execute("SELECT competition FROM result WHERE comp_id=? LIMIT 1", (cid,)).fetchone()
                   or con.execute("SELECT competition FROM future WHERE comp_id=? LIMIT 1", (cid,)).fetchone())
            name = row[0] if row else ""
            html = S.get(f"{S.LOGLIG}/LeagueTable/AthleticsDisciplines/{cid}")
            d = S.loglig_date(html) if html else None
            pool = S.detect_course(cid, name)
            if d:
                con.execute("UPDATE result SET date=?, pool_length=? WHERE comp_id=?", (d, pool, cid))
                con.execute("UPDATE future SET date=?, pool_length=? WHERE comp_id=?", (d, pool, cid))
                fixed_dates += 1
            else:
                con.execute("UPDATE result SET pool_length=? WHERE comp_id=?", (pool, cid))
                con.execute("UPDATE future SET pool_length=? WHERE comp_id=?", (pool, cid))
            if i % 50 == 0:
                con.commit(); print(f"  {i}/{len(cids)} ...")
            time.sleep(S.SLEEP)
        con.commit()
        print(f"Set dates on {fixed_dates} competitions.")

    n = DB.export(con, is_seed=False)
    print(f"Re-exported data.js + swimmers.json — {n} swimmers. Reload the app.")

if __name__ == "__main__":
    main()
