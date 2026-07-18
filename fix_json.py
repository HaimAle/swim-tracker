# -*- coding: utf-8 -*-
"""
fix_json.py  —  Fix garbled Hebrew text in an existing swimmers.json / data.js,
                WITHOUT touching the database or re-crawling. Nothing is deleted.

The garbled text is UTF-8 that was decoded as Latin-1. That is losslessly
reversible: re-encode as Latin-1, decode as UTF-8. Strings that are already
correct Hebrew can't be encoded to Latin-1, so they're left untouched.

Usage:
    python fix_json.py                 # reads swimmers.json
    python fix_json.py data.js         # or point it at any data.js / swimmers.json
    python fix_json.py path/to/file.json

Outputs (originals are left untouched):
    swimmers.fixed.json   — corrected copy
    data.js               — corrected, ready for index.html
"""
import json, re, sys, os

HEB = re.compile(r"[֐-׿]")

def fix(s):
    if not isinstance(s, str) or not s:
        return s
    try:
        f = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    return f if (f != s and HEB.search(f)) else s

def load(path):
    t = open(path, encoding="utf-8").read()
    if "window.SWIM_DATA" in t:                       # it's a data.js
        t = t[t.index("{"): t.rstrip().rstrip(";").rindex("}") + 1]
    return json.loads(t)

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "swimmers.json"
    if not os.path.exists(path):
        print(f"File not found: {path}"); sys.exit(1)
    d = load(path)
    changed = 0
    for s in d.get("swimmers", {}).values():
        for rec in s.get("past", []) + s.get("future", []):
            for k in ("competition", "event", "category", "club"):
                if k in rec:
                    nv = fix(rec[k])
                    if nv != rec[k]:
                        rec[k] = nv; changed += 1
        nv = fix(s.get("displayName", ""))
        if nv != s.get("displayName"):
            s["displayName"] = nv; changed += 1
        s["clubs"] = [fix(c) for c in s.get("clubs", [])]

    json.dump(d, open("swimmers.fixed.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open("data.js", "w", encoding="utf-8") as f:
        f.write("// Text-fixed by fix_json.py\nwindow.SWIM_DATA = ")
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print(f"Fixed {changed} garbled text fields across {len(d.get('swimmers',{}))} swimmers.")
    print("Wrote swimmers.fixed.json + data.js (your original file was not modified).")

if __name__ == "__main__":
    main()
