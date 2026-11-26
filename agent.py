import os, re, json, hashlib, sys
import requests, yaml
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "tr-TR,tr;q=0.9"}
STATE = "seen.json"
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")

def fold(s):
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return s.translate(str.maketrans("çşğıöü", "csgiou"))

SETTINGS, RESULTS, LASTRUN = "docs/settings.json", "docs/results.json", "last_run.txt"

def load_cfg():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.path.exists(SETTINGS):
        st = json.load(open(SETTINGS, encoding="utf-8"))
        for k in ("budget_max", "min_rooms", "allow_furnished", "max_building_age",
                  "attic_needs_elevator", "send_min_score", "digest_min_score", "frequency_minutes"):
            if k in st: cfg[k] = st[k]
        amap = {al: t for t, v in cfg["tiers"].items() for al in v["aliases"]}
        amap.update(st.get("areas", {}))            # {alias: 0|1|2|3}; 0 = kapalı
        for t in cfg["tiers"]: cfg["tiers"][t]["aliases"] = [a for a, x in amap.items() if int(x) == int(t)]
    return cfg

# ---------- KAYNAKLAR ----------
def fetch_listing_site(src):
    r = requests.get(src["url"], headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        if not re.search(src["link_pattern"], a["href"]):
            continue
        link = a["href"] if a["href"].startswith("http") else src["base"] + a["href"]
        if link in seen:
            continue
        card = a
        for _ in range(5):   # kartı bulmak için yukarı tırman
            if card.parent is None: break
            card = card.parent
            if len(card.get_text(" ", strip=True)) > 80 and re.search(r"₺|TL", card.get_text()):
                break
        text = card.get_text(" ", strip=True)
        if len(text) < 30: continue
        seen.add(link)
        out.append({"source": src["name"], "url": link, "text": text})
    return out

def fetch_telegram_channel(src):
    r = requests.get(f"https://t.me/s/{src['channel']}", headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for m in soup.select(".tgme_widget_message"):
        t = m.select_one(".tgme_widget_message_text")
        a = m.select_one("a.tgme_widget_message_date")
        if t and a:
            out.append({"source": f"TG:{src['channel']}", "url": a["href"],
                        "text": t.get_text(" ", strip=True)})
    return out

FETCHERS = {"listing_site": fetch_listing_site, "telegram_channel": fetch_telegram_channel}

# ---------- ANALİZ ----------
def parse(text):
    t = fold(text)
    price = None
    m = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d{4,6})\s*(?:₺|tl)", t)
    if m: price = int(re.sub(r"[.,]", "", m.group(1)))
    rooms = None
    m = re.search(r"\b(\d)\s*\+\s*(\d)\b", t)
    if m: rooms = int(m.group(1))
    age = None
    m = re.search(r"(\d{1,2})\s*yasinda", t) or re.search(r"bina yasi\D{0,6}(\d{1,2})", t)
    if m: age = int(m.group(1))
    elif "sifir bina" in t or "yeni bina" in t: age = 0
    return t, price, rooms, age

def score(item, cfg):
    t, price, rooms, age = parse(item["text"])
    notes, pts = [], 0
    if re.search(r"paylasimli|\byurt\b|ogrenci|erkek|bayan|ozel oda|odalar mevcut|gunluk|devren|ortak mutfak|ortak oda", t): return None

    # --- eleme kuralları ---
    if price and price > cfg["budget_max"]: return None
    if rooms is not None and rooms < cfg["min_rooms"]: return None
    if not cfg["allow_furnished"] and re.search(r"(?<!es)(?<!un)\besyali\b", t) and "esyasiz" not in t: return None
    if age is not None and age > cfg["max_building_age"]: return None
    attic = bool(re.search(r"cati kat|cati dubleks", t))
    elevator = bool(re.search(r"asansor", t)) and "asansorsuz" not in t and "asansor yok" not in t
    if attic and cfg["attic_needs_elevator"] and not elevator: return None

    # --- mahalle ---
    tier_hit = None
    for tier in sorted(cfg["tiers"]):
        for al in cfg["tiers"][tier]["aliases"]:
            if al in t:
                tier_hit = (tier, al); break
        if tier_hit: break
    if not tier_hit: return None
    tier, alias = tier_hit
    pts += cfg["tiers"][tier]["points"]
    notes.append(f"📍 Bölge önceliği: {tier}. kademe ({alias})")
    for key, rule in cfg.get("conditional", {}).items():
        if key in t and not any(n in t for n in rule["need"]):
            pts -= rule["penalty"]
            notes.append(f"⚠️ {key}: ana cadde/sanayi yakınlığı ilanda doğrulanamadı")
        elif key in t:
            pts += 10; notes.append(f"✅ {key}: yakınlık ifadesi var")
    if tier == 1 and re.search(r"metro|metrobus|ana cadde|ulasim", t):
        pts += 8; notes.append("🚇 Ulaşım vurgusu var")

    # --- fiyat ---
    if price:
        pts += round(15 * (1 - price / cfg["budget_max"]) * 2) if price < cfg["budget_max"] else 0
        pts = pts  # düşük fiyat = az bonus
        notes.append(f"💰 {price:,} TL".replace(",", "."))
    else:
        notes.append("💰 Fiyat okunamadı")
    # --- diğer ---
    if rooms is None: pts -= 20; notes.append("⚠️ Oda sayısı okunamadı")
    elif rooms >= 3: pts += 5
    if age is not None:
        pts += 10 if age <= 10 else 5
        notes.append(f"🏢 Bina yaşı: {age}")
    else:
        notes.append("🏢 Bina yaşı belirtilmemiş")
    if elevator: pts += 5; notes.append("🛗 Asansör var")
    if attic: notes.append("🏠 Çatı katı (asansör var)")
    return min(pts, 100), notes, {"price": price, "rooms": rooms, "age": age, "tier": tier}

# ---------- TELEGRAM ----------
def send(msg):
    if not TOKEN or not CHAT:
        print(msg); return
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT, "text": msg, "disable_web_page_preview": False},
                  timeout=30)

def main():
    import time, datetime
    cfg = load_cfg()
    freq = int(cfg.get("frequency_minutes", 60))
    if not os.environ.get("FORCE") and os.path.exists(LASTRUN):
        if time.time() - float(open(LASTRUN).read() or 0) < freq * 60 - 300:
            print("Henüz zamanı gelmedi"); return
    open(LASTRUN, "w").write(str(time.time()))
    seen = set(json.load(open(STATE))) if os.path.exists(STATE) else set()
    errors, found = [], []
    for src in cfg["sources"]:
        try:
            items = FETCHERS[src["type"]](src)
            print(f"{src['name']}: {len(items)} ilan"); found += items
        except Exception as e:
            errors.append(f"{src['name']}: {e}")
    results = []
    for it in found:
        h = hashlib.md5(it["url"].encode()).hexdigest()
        if h in seen: continue
        seen.add(h)
        r = score(it, cfg)
        if r: results.append((r[0], r[1], it, r[2]))
    results.sort(key=lambda x: -x[0])

    for sc, notes, it, _ in [r for r in results if r[0] >= cfg["send_min_score"]]:
        send(f"🏡 Uyum: %{sc}  ({it['source']})\n" + "\n".join(notes) + f"\n{it['url']}")
    mid = [r for r in results if cfg["digest_min_score"] <= r[0] < cfg["send_min_score"]]
    if mid:
        send("📋 Orta uyumlu ilanlar:\n" + "\n".join(f"%{r[0]} – {r[2]['url']}" for r in mid))
    if errors and not found:
        send("⚠️ Hiçbir kaynaktan ilan alınamadı:\n" + "\n".join(errors))

    old = json.load(open(RESULTS, encoding="utf-8"))["items"] if os.path.exists(RESULTS) else []
    now = datetime.datetime.utcnow().isoformat(timespec="minutes") + "Z"
    new = [{"score": sc, "url": it["url"], "source": it["source"], "notes": n, "found": now, **m}
           for sc, n, it, m in results]
    items = (new + old)[:300]
    json.dump({"updated": now, "errors": errors, "items": items},
              open(RESULTS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(sorted(seen), open(STATE, "w"))

if __name__ == "__main__":
    main()
