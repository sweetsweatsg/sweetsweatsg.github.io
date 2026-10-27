"""One-off probe: finds out how fie.org exposes athlete + ranking data.
Writes everything it learns to fie_probe.txt. Changes nothing else."""
import json, sys, requests
out = []
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "en"}

log("== 1. Plain HTTP requests ==")
for u in ["https://fie.org/", "https://fie.org/athletes", "https://fie.org/robots.txt",
          "https://fie.org/athletes/ranking", "https://fie.org/rankings"]:
    try:
        r = requests.get(u, headers=H, timeout=30)
        log(f"GET {u} -> {r.status_code} {r.headers.get('content-type')} {len(r.text)} chars")
        log("   ", r.text[:300].replace("\n", " "))
    except Exception as e:
        log(f"GET {u} -> ERROR {e}")

log("\n== 2. Guessed athlete search API (POST) ==")
body = {"name": "", "status": "", "gender": ["F"], "weapon": ["S"], "type": ["I"],
        "country": "SGP", "level": "S", "isNational": False, "fetchPage": 1}
for u in ["https://fie.org/athletes", "https://fie.org/athletes/search"]:
    try:
        r = requests.post(u, json=body, headers={**H, "Accept": "application/json"}, timeout=30)
        log(f"POST {u} -> {r.status_code} {r.headers.get('content-type')} {len(r.text)} chars")
        log("   ", r.text[:600].replace("\n", " "))
    except Exception as e:
        log(f"POST {u} -> ERROR {e}")

log("\n== 3. Real browser: record every data request the page makes ==")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})
        seen = []
        def on_resp(r):
            ct = r.headers.get("content-type", "")
            if r.request.resource_type in ("xhr", "fetch") or "json" in ct:
                try: txt = r.text()[:700].replace("\n", " ")
                except Exception: txt = "(unreadable)"
                seen.append((r.request.method, r.url, r.status, ct, txt, (r.request.post_data or "")[:300]))
        pg.on("response", on_resp)
        for u in ["https://fie.org/athletes", "https://fie.org/athletes?country=SGP"]:
            try:
                pg.goto(u, wait_until="networkidle", timeout=60000)
                pg.wait_for_timeout(3000)
                log(f"PAGE {u} title={pg.title()!r}")
                log("   text:", pg.inner_text("body")[:700].replace("\n", " | "))
                links = pg.eval_on_selector_all("a[href*='athlete']", "e=>e.slice(0,15).map(a=>a.href+' '+a.innerText.trim().slice(0,40))")
                for l in links: log("   link:", l)
                pg.screenshot(path="fie_page.png")
            except Exception as e:
                log(f"PAGE {u} -> ERROR {e}")
        log(f"\n{len(seen)} data requests seen:")
        for m, u, s, ct, txt, post in seen:
            log(f"- {m} {u} -> {s} {ct}")
            if post: log("    body:", post)
            log("    resp:", txt)
        b.close()
except Exception as e:
    log("Playwright step failed:", e)

open("fie_probe.txt", "w").write("\n".join(out))
