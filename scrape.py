#!/usr/bin/env python3
"""Fetch Fencing Singapore women's sabre rankings (Cadet/Junior/Senior) -> data.json.

Runs daily on GitHub Actions. It fails loudly (non-zero exit) if the page layout
changes or the numbers don't add up, rather than publishing wrong data.
"""
import datetime as dt
import hashlib
import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup

URL = "https://my.fencingsingapore.org.sg/showranks"
CATS = ["Cadet", "Junior", "Senior"]
GENDERS = {"women": "Female", "men": "Male"}
WEAPONS = {"sabre": "Sabre", "foil": "Foil"}
HEADERS = {"User-Agent": "sabre-dashboard/1.0 (personal project; 3 requests per day)"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def season_start(today=None):
    """Seasons start 1 June: Oct 2026 -> 2026, Mar 2027 -> 2026."""
    t = today or dt.date.today()
    return t.year if t.month >= 6 else t.year - 1


def fetch(cat, season, gender="Female", weapon="Sabre"):
    params = {
        "rankingmethod[category]": cat,
        "rankingmethod[gender]": gender,
        "rankingmethod[weapon]": weapon,
        "rankingmethod[windowlength]": season,
        "commit": "Search",
    }
    for attempt in range(3):
        try:
            r = requests.get(URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def points(text):
    m = re.search(r"/\s*(\d+)", text)  # "2 / 1251" -> 1251 ; "-" -> 0
    return int(m.group(1)) if m else 0


def num(text):
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def comp_id(a):
    return int(re.search(r"/competitions/(\d+)", a["href"]).group(1))


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("no ranking table on page")
    rows = table.find_all("tr")
    hi = None
    for i, r in enumerate(rows):
        c = r.find(["th", "td"])
        if c is not None and c.get_text(strip=True) == "Ranking":
            hi = i
            break
    if hi is None:
        raise ValueError("header row not found")

    # competition id -> date, taken from the date links above the header row
    dates = {}
    for r in rows[:hi]:
        for a in r.find_all("a", href=re.compile(r"/competitions/\d+")):
            if DATE.fullmatch(a.get_text(strip=True)):
                dates[comp_id(a)] = a.get_text(strip=True)

    head = rows[hi].find_all(["th", "td"])
    events = []  # (column index, date, event name)
    for i, c in enumerate(head):
        a = c.find("a", href=re.compile(r"/competitions/\d+"))
        if a:
            cid = comp_id(a)
            if cid not in dates:
                raise ValueError(f"no date found for competition {cid}")
            events.append((i, dates[cid], a.get_text(strip=True), cid))
    events.sort(key=lambda e: (e[1], e[0]))

    fencers = []
    for r in rows[hi + 1:]:
        tds = r.find_all("td")
        if len(tds) != len(head):
            continue
        link = tds[1].find("a", href=re.compile(r"/profiles/\d+"))
        if link is None:
            continue
        fencers.append({
            "id": int(re.search(r"/profiles/(\d+)", link["href"]).group(1)),
            "name": link.get_text(strip=True),
            "club": tds[2].get_text(strip=True),
            "school": tds[3].get_text(strip=True),
            "star": "*" in tds[0].get_text(),  # rank shown as "7*" on the site
            "base": num(tds[-2].get_text()),
            "total": num(tds[-1].get_text()),
            "pts": [points(tds[i].get_text(" ", strip=True)) for i, *_ in events],
        })
    return events, fencers


def best_n(html):
    """How many event scores count towards the total (4 or 5). Read from the 'Total Best (N) Scores' header:
    it differs by weapon and level (e.g. 5 for Junior and Senior foil, 4 for sabre)."""
    m = re.search(r"Best\s*\((\d+)\)", BeautifulSoup(html, "html.parser").get_text(" "))
    return int(m.group(1)) if m else 4


def check(cat, fencers, n=4):
    """Total must equal best-N event scores + base points (verified by hand on every level read so far)."""
    if len(fencers) < 5:
        raise ValueError(f"{cat}: only {len(fencers)} fencers parsed")
    bad = [f["name"] for f in fencers
           if sum(sorted(f["pts"], reverse=True)[:n]) + f["base"] != f["total"]]
    if len(bad) > 0.1 * len(fencers):
        raise ValueError(f"{cat}: {len(bad)} rows fail the best-{n} + base check, e.g. {bad[:3]}")
    return bad


def label(name, words):
    name = re.sub(r"\(.*?\)", "", name)
    sur, _, given = name.partition(",")
    return " ".join((sur.split() + given.split()[:words])).title()


def build(cat, season, gender="Female", weapon="Sabre"):
    html = fetch(cat, season, gender, weapon)
    events, fencers = parse(html)
    bn = best_n(html)
    bad = check(cat, fencers, bn)
    if bad:
        print(f"WARNING {cat}: total mismatch for {bad}")
    keep = sorted((f for f in fencers if f["total"] > 0), key=lambda f: -f["total"])
    short = [label(f["name"], 1) for f in keep]
    out = [[f["id"], label(f["name"], 2 if short.count(sh) > 1 else 1), f["club"], f["base"], f["pts"],
            label(f["name"], 99), f["school"], 1 if f["star"] else 0]
           for f, sh in zip(keep, short)]
    return {"ev": [[d, n, cid] for _, d, n, cid in events], "f": out, "b": bn,
            "st": sorted(f["id"] for f in fencers if f["star"])}


SEASON_ROW = re.compile(r"^[MW][SFE] \w+ \d{4}-\d{4}$")  # season-final ranking records, not competitions


def profile_events(html):
    """Competition history and season-end rankings from a profile page.
    Birth year, school and citizenship on the page are deliberately ignored."""
    soup = BeautifulSoup(html, "html.parser")
    events, ranks = None, []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        heads = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])] if rows else []
        ix = {h: i for i, h in enumerate(heads) if h}
        if "Finalrank" in ix and "Competition" in ix:
            events = []
            for r in rows[1:]:
                tds = r.find_all("td")
                if len(tds) != len(heads):
                    continue
                g = lambda h: tds[ix[h]].get_text(strip=True)
                name = g("Competition")
                if SEASON_ROW.match(name):
                    continue
                a = tds[ix["Competition"]].find("a", href=re.compile(r"/competitions/\d+"))
                cid = int(re.search(r"/competitions/(\d+)", a["href"]).group(1)) if a else 0
                events.append([g("Event Date"), name, g("Location"), g("Category"),
                               g("Points For"), num(g("Finalrank")), num(g("Points")), cid])
        elif "Season Starting" in ix and "Age Group" in ix:
            for r in rows[1:]:
                tds = r.find_all("td")
                if len(tds) != len(heads):
                    continue
                g = lambda h: tds[ix[h]].get_text(strip=True)
                ranks.append([num(g("Season Starting")), g("Age Group"), g("Weapon Type"),
                              g("Gender"), num(g("Ranking")), num(g("Points"))])
    if not events:
        raise ValueError("no competition table on profile")
    return sorted(events, key=lambda e: e[0], reverse=True), ranks


def past_participants(season, current_ids):
    """Fencers ranked in earlier seasons (via the site's season selector) who are not ranked now.
    Only used to discover profile ids; ranks themselves come from each profile."""
    found = {}
    for wname, w in WEAPONS.items():
        for gname, g in GENDERS.items():
            for cat in CATS:
                for s in range(max(2022, season - 5), season):
                    try:
                        time.sleep(3)
                        _, fencers = parse(fetch(cat, s, g, w))
                    except Exception as e:
                        print(f"WARNING past season {wname} {gname} {cat} {s}: {e}")
                        continue
                    for f in fencers:
                        if f["total"] > 0 and f["id"] not in current_ids:
                            found[f["id"]] = [label(f["name"], 1), label(f["name"], 99), f["club"]]
    print("past participants found:", len(found))
    return found


def datasets(data):
    """Yield (weapon, gender, level, dataset) for every ranking table in data."""
    for w, wd in data.get("weapons", {}).items():
        for g, gd in wd.items():
            for cat, ds in gd.items():
                yield w, g, cat, ds


def update_profiles(data, now, alumni):
    """Cache each ranked fencer's full competition history in profiles.json.
    A profile is re-fetched only when her results changed or the copy is over 7 days old.
    Failures here never block the rankings update."""
    old = {}
    if os.path.exists("profiles.json"):
        with open("profiles.json", encoding="utf-8") as fh:
            old = json.load(fh).get("p", {})
    sigs = {}
    for w, g, cat, ds in datasets(data):
        tag = [] if (w, g) == ("sabre", "women") else (["m"] if (w, g) == ("sabre", "men") else [w, g])
        for f in ds.get("f", []):
            sigs.setdefault(str(f[0]), []).append(tag + [cat.capitalize(), f[4]])
    for fid in alumni:
        sigs.setdefault(str(fid), ["past"])  # past participants: history no longer changes
    today = now.date()
    new, fetched, failed = {}, 0, 0
    for fid, sig in sigs.items():
        s = hashlib.md5(json.dumps([sig, "v2"]).encode()).hexdigest()[:10]
        cur = old.get(fid)
        if cur and cur["s"] == s and (today - dt.date.fromisoformat(cur["t"])).days < (90 if sig == ["past"] else 7):
            new[fid] = cur
            continue
        try:
            time.sleep(2)
            r = requests.get(f"https://my.fencingsingapore.org.sg/profiles/{fid}", headers=HEADERS, timeout=30)
            r.raise_for_status()
            ev, rk = profile_events(r.text)
            new[fid] = {"s": s, "t": today.isoformat(), "e": ev, "r": rk}
            fetched += 1
        except Exception as e:  # keep the old copy rather than lose history
            failed += 1
            print(f"WARNING profile {fid}: {e}")
            if cur:
                new[fid] = cur
    for fid, n in alumni.items():
        if str(fid) in new:
            new[str(fid)]["n"] = n
    if failed:
        print(f"::warning::{failed} of {len(sigs)} profile pages could not be read")
    if not os.path.exists("profiles.json") or json.dumps({"p": new}, sort_keys=True) != json.dumps({"p": old}, sort_keys=True):
        with open("profiles.json", "w", encoding="utf-8") as fh:
            json.dump({"p": new}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"profiles.json: {fetched} fetched, {failed} failed, {len(new)} total")


def competition_page(html):
    """Full results of one competition: every fencer's final place and points."""
    soup = BeautifulSoup(html, "html.parser")
    meta = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2 and cells[0].get_text(strip=True).endswith(":"):
            meta[cells[0].get_text(strip=True).rstrip(":")] = cells[1].get_text(strip=True)
    rows, cat = [], ""
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        heads = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])] if trs else []
        if "Finalrank" not in heads or "Fencer" not in heads:
            continue
        ix = {h: i for i, h in enumerate(heads) if h}
        for r in trs[1:]:
            tds = r.find_all("td")
            if len(tds) != len(heads):
                continue
            a = tds[ix["Fencer"]].find("a", href=re.compile(r"/profiles/\d+"))
            if a is None:
                continue
            if "Category" in ix:
                cat = cat or tds[ix["Category"]].get_text(strip=True)
            rows.append([num(tds[ix["Finalrank"]].get_text()), num(tds[ix["Points"]].get_text()),
                         int(re.search(r"/profiles/(\d+)", a["href"]).group(1)), label(a.get_text(strip=True), 99)])
    if not rows:
        raise ValueError("no results table")
    return {"n": meta.get("Name", ""), "d": meta.get("Eventdate", ""), "l": meta.get("Location", ""),
            "pf": meta.get("Points For", ""), "k": cat, "t": num(meta.get("Totalfencers", "")) or None,
            "df": meta.get("Difficulty", ""), "r": rows}


def update_competitions(data, now):
    """Full results for every competition this season (competitions.json).
    Finished events are read once; events from the last 10 days are re-read in case of corrections."""
    old = {}
    if os.path.exists("competitions.json"):
        with open("competitions.json", encoding="utf-8") as fh:
            old = json.load(fh).get("c", {})
    events = {}
    for w, g, cat, ds in datasets(data):
        for d, n, cid in ds.get("ev", []):
            events[str(cid)] = d
    today = now.date()
    new, fetched, failed = {}, 0, 0
    for cid, d in sorted(events.items(), key=lambda kv: kv[1]):
        cur = old.get(cid)
        if cur and (today - dt.date.fromisoformat(d)).days >= 10:
            new[cid] = cur
            continue
        try:
            time.sleep(2)
            r = requests.get(f"https://my.fencingsingapore.org.sg/competitions/{cid}", headers=HEADERS, timeout=30)
            r.raise_for_status()
            new[cid] = competition_page(r.text)
            fetched += 1
        except Exception as e:  # keep the old copy rather than lose it
            failed += 1
            print(f"WARNING competition {cid}: {e}")
            if cur:
                new[cid] = cur
    if failed:
        print(f"::warning::{failed} of {len(events)} competition pages could not be read")
    if not os.path.exists("competitions.json") or json.dumps({"c": new}, sort_keys=True) != json.dumps({"c": old}, sort_keys=True):
        with open("competitions.json", "w", encoding="utf-8") as fh:
            json.dump({"c": new}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"competitions.json: {fetched} fetched, {failed} failed, {len(new)} total")


def save_snapshot(data, season, now):
    """One real snapshot per day: [id, base, total]. Base points shrink over time,
    so this is the only accurate record of what the site showed on each day."""
    snaps = []
    if os.path.exists("history.json"):
        with open("history.json", encoding="utf-8") as fh:
            snaps = json.load(fh).get("snaps", [])
    today = now.strftime("%Y-%m-%d")
    snap = {"d": today, "s": season}
    for w, g, cat, ds in datasets(data):
        key = f"{g}_{cat}" if w == "sabre" else f"{w}_{g}_{cat}"
        k = ds.get("b", 4)
        snap[key] = [[f[0], f[3], f[3] + sum(sorted(f[4], reverse=True)[:k])] for f in ds.get("f", [])]
    snaps = [x for x in snaps if x.get("d") != today] + [snap]
    with open("history.json", "w", encoding="utf-8") as fh:
        json.dump({"snaps": snaps}, fh, separators=(",", ":"))
    print("history.json:", len(snaps), "snapshots")


def main():
    season = int(os.environ.get("SEASON") or season_start())
    old = None
    if os.path.exists("data.json"):
        with open("data.json", encoding="utf-8") as fh:
            old = json.load(fh)

    def old_group(w, g):  # previous copy of one weapon/gender group (also reads the older file layouts)
        if not old:
            return {}
        if "weapons" in old:
            return old["weapons"].get(w, {}).get(g) or {}
        return (old.get(g) or {}) if w == "sabre" else {}

    weapons = {w: {} for w in WEAPONS}
    stars, first = set(), True
    for wname, w in WEAPONS.items():
        for gname, g in GENDERS.items():
            try:
                got = {}
                for cat in CATS:
                    if not first:
                        time.sleep(3)  # be gentle with the server
                    first = False
                    got[cat.lower()] = build(cat, season, g, w)
                weapons[wname][gname] = got
                stars |= {i for c in got.values() for i in c.pop("st")}  # fencers marked * on the site
            except Exception as e:
                if (wname, gname) == ("sabre", "women"):
                    raise  # the main dashboard must never publish wrong or partial data
                print(f"::warning::{wname} {gname} could not be read, keeping the previous copy: {e}")
                weapons[wname][gname] = old_group(wname, gname)
                stars |= set((old or {}).get("stars", []))
    data = {"weapons": {w: {g: v for g, v in gd.items() if v} for w, gd in weapons.items() if any(gd.values())},
            "season": season, "stars": sorted(stars)}
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))

    if old and {k: v for k, v in old.items() if k != "generated"} == data:
        print("data.json: no change")
    else:
        data["generated"] = now.strftime("%Y-%m-%d %H:%M")
        with open("data.json", "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        print("Updated data.json")
    save_snapshot(data, season, now)
    try:
        update_competitions(data, now)
    except Exception as e:  # never block the rankings update
        print(f"::warning::competition step failed: {e}")
    try:
        current_ids = {f[0] for w, g, c, ds in datasets(data) for f in ds.get("f", [])}
        update_profiles(data, now, past_participants(season, current_ids))
    except Exception as e:  # never block the rankings update
        print(f"::warning::profile step failed: {e}")


if __name__ == "__main__":
    main()
