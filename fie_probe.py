"""Probe 2: fetch the FIE pages directly (no browser) and report what is inside the page data."""
import re, requests
out = []
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "en"}
BASE = "https://fie.org"
URLS = [
  "/athletes?country=SGP&weapon=S&gender=F",
  "/athletes?weapon=S&gender=F",
  "/athletes/detailed-ranking?season=2027&weapon=S&gender=F&category=S&type=I",
  "/athletes/detailed-ranking?season=2027&weapon=S&gender=F&category=J&type=I",
  "/athletes/54369",
]
for path in URLS:
    log("\n" + "=" * 70); log("GET", BASE + path)
    try:
        r = requests.get(BASE + path, headers=H, timeout=60)
    except Exception as e:
        log("ERROR", e); continue
    t = r.text
    log("status", r.status_code, "chars", len(t))
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', t, re.S)
    log("NUXT_DATA present:", bool(m), "len", len(m.group(1)) if m else 0)
    if m:
        d = m.group(1)
        log("NUXT_DATA start:", d[:2500])
        sg = [x.start() for x in re.finditer(r'SGP', d)]
        log("SGP occurrences in data:", len(sg))
        for i in sg[:4]:
            log("  ...", d[max(0, i-250): i+250].replace("\n", " "))
    links = re.findall(r'href="(/athletes/\d+)"[^>]*>(.*?)</a>', t, re.S)
    log("athlete links in HTML:", len(links))
    for h, tx in links[:12]:
        log("  ", h, re.sub(r"<[^>]+>", " ", tx).strip()[:60])
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    body = re.sub(r"<[^>]+>", " | ", body)
    body = re.sub(r"\s+", " ", body)
    log("visible text:", body[:1200])
open("fie_probe.txt", "w").write("\n".join(out))
