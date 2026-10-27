"""Match Singapore fencers in data.json to their FIE world ranking.
Writes fie.json (used by the site) and fie_review.txt (for you to check).
Never overwrites data.json. If FIE cannot be reached it keeps the old fie.json."""
import json, os, re, sys, time, datetime, difflib
import requests

SEASON = 2027                      # FIE season label for 2026/27
WEAPONS = {"S": "sabre", "F": "foil", "E": "epee"}
GENDERS = {"F": "women", "M": "men"}
CATS = {"S": "senior", "J": "junior", "C": "cadet"}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
sys.setrecursionlimit(20000)
review = []
def log(*a):
    s = " ".join(str(x) for x in a); print(s); review.append(s)

# ---------- reading the page data (Nuxt "devalue" format) ----------
def decode_nuxt(html):
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m: return None
    return json.loads(m.group(1))

def make_resolver(table):
    memo = {}
    def res(i):
        if not isinstance(i, int): return i
        if i < 0: return None
        if i in memo: return memo[i]
        v = table[i]
        if isinstance(v, dict):
            out = {}; memo[i] = out
            for k, x in v.items(): out[k] = res(x)
        elif isinstance(v, list):
            if v and isinstance(v[0], str):          # special tuple, e.g. ["Reactive", n]
                memo[i] = res(v[1]) if len(v) > 1 and isinstance(v[1], int) else None
            else:
                out = []; memo[i] = out
                for x in v: out.append(res(x))
        else:
            memo[i] = v
        return memo[i]
    return res

def get_fencers(html):
    table = decode_nuxt(html)
    if not table: return None
    res = make_resolver(table)
    for i, v in enumerate(table):
        if isinstance(v, dict) and "fencers" in v and "competitions" in v:
            f = res(v["fencers"])
            if isinstance(f, list): return [x for x in f if isinstance(x, dict)]
    return None

def fetch_ranking(w, g, c):
    url = f"https://fie.org/athletes/detailed-ranking?season={SEASON}&weapon={w}&gender={g}&category={c}&type=I"
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=90)
            if r.status_code == 200:
                return get_fencers(r.text)
            log(f"  {url} -> HTTP {r.status_code}")
        except Exception as e:
            log(f"  {url} -> {e}")
        time.sleep(3 * (attempt + 1))
    return None

# ---------- name matching ----------
def toks(name):
    s = re.sub(r"[^a-z\s]", " ", (name or "").lower().replace("-", " "))
    return [t for t in s.split() if t]

def score(a, b):
    A, B = set(toks(a)), set(toks(b))
    if not A or not B: return 0
    if A == B: return 1.0
    sm, lg = (A, B) if len(A) <= len(B) else (B, A)
    if len(sm) >= 2 and sm <= lg: return 0.9
    r = difflib.SequenceMatcher(None, " ".join(sorted(A)), " ".join(sorted(B))).ratio()
    return round(r * 0.85, 3) if r >= 0.88 else 0

def main():
    data = json.load(open("data.json"))
    overrides = json.load(open("fie_map.json")) if os.path.exists("fie_map.json") else {}
    old = json.load(open("fie.json")) if os.path.exists("fie.json") else None

    fie = {}   # (weapon, gender) -> {addrId: {name, ranks{cat:[rank, pts, of]}}}
    ok = 0
    for w, wn in WEAPONS.items():
        for g, gn in GENDERS.items():
            for c, cn in CATS.items():
                fs = fetch_ranking(w, g, c)
                time.sleep(1.5)
                if fs is None:
                    log(f"WARNING: could not read {wn} {gn} {cn}"); continue
                ok += 1
                sg = [x for x in fs if x.get("countryCode") == "SGP"]
                log(f"{wn} {gn} {cn}: {len(fs)} fencers ranked, {len(sg)} from Singapore")
                d = fie.setdefault((wn, gn), {})
                for x in sg:
                    a = d.setdefault(x["addrId"], {"name": x["name"], "ranks": {}})
                    try: pts = float(x["points"])
                    except Exception: pts = None
                    a["ranks"][c] = [x["rank"], pts, len(fs)]
    if ok == 0:
        log("FIE could not be read at all. Keeping the previous fie.json."); write_review(); return

    matches, rev, unmatched, used = {}, [], [], set()
    for wn in WEAPONS.values():
        for gn in GENDERS.values():
            cands = fie.get((wn, gn), {})
            seen = {}
            for lvl in CATS.values():
                for f in data["weapons"].get(wn, {}).get(gn, {}).get(lvl, {}).get("f", []):
                    seen.setdefault(f[0], f)
            for lid, f in seen.items():
                key = f"{wn}-{gn}-{lid}"
                name = f[5] if len(f) > 5 and f[5] else f[1]
                if key in overrides:
                    fid = overrides[key]
                    if fid and fid in cands:
                        matches[key] = {"fie": fid, "name": cands[fid]["name"], "r": cands[fid]["ranks"], "how": "manual"}
                        used.add((wn, gn, fid))
                    continue
                sc = sorted(((score(name, c["name"]), fid) for fid, c in cands.items()), reverse=True)
                sc = [s for s in sc if s[0] > 0]
                if sc and sc[0][0] >= 0.9 and (len(sc) == 1 or sc[1][0] < 0.9):
                    fid = sc[0][1]
                    matches[key] = {"fie": fid, "name": cands[fid]["name"], "r": cands[fid]["ranks"], "how": "auto"}
                    used.add((wn, gn, fid))
                elif sc:
                    rev.append((key, name, [(cands[i]["name"], i, s) for s, i in sc[:3]]))
                else:
                    unmatched.append((key, name, f[2] if len(f) > 2 else ""))

    log(f"\n=== SUMMARY: {len(matches)} matched automatically or manually, {len(rev)} need your review, {len(unmatched)} have no FIE match ===")
    log("\n=== MATCHED (check a few by eye) ===")
    for k, m in sorted(matches.items()):
        nm = next((f[5] if len(f) > 5 and f[5] else f[1] for f in [x for lv in CATS.values() for x in data["weapons"][k.split('-')[0]][k.split('-')[1]].get(lv, {}).get("f", []) if str(x[0]) == k.split('-', 2)[2]]), "?")
        log(f"{k}: {nm}  ->  {m['name']} (FIE {m['fie']})  {m['how']}  ranks {m['r']}")
    log("\n=== NEEDS REVIEW (possible matches, not used) ===")
    for k, n, c in rev: log(f"{k}: {n}  ?  " + "; ".join(f"{a} (FIE {b}, score {s})" for a, b, s in c))
    log("\n=== NO MATCH (not on FIE for that weapon, or name differs a lot) ===")
    for k, n, cl in unmatched: log(f"{k}: {n} ({cl})")
    log("\n=== SINGAPORE ON FIE BUT NOT LINKED TO ANYONE ON YOUR SITE ===")
    for (wn, gn), d in fie.items():
        for fid, a in d.items():
            if (wn, gn, fid) not in used: log(f"{wn} {gn}: {a['name']} (FIE {fid}) ranks {a['ranks']}")

    out = {"generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", "season": SEASON, "m": matches}
    json.dump(out, open("fie.json", "w"), separators=(",", ":"))
    write_review()

def write_review():
    open("fie_review.txt", "w").write("\n".join(review))

if __name__ == "__main__":
    try: main()
    except Exception as e:
        log("FIE sync failed:", repr(e)); write_review()
