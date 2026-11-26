import os, re, json, hashlib, sys
import requests, yaml
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "tr-TR,tr;q=0.9"}
from pathlib import Path

DATA_DIR = Path(os.environ.get("KIRA_DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE = str(DATA_DIR / "seen.json")
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")

def fold(s):
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return s.translate(str.maketrans("çşğıöü", "csgiou"))

SETTINGS = str(DATA_DIR / "settings.json")
RESULTS = str(DATA_DIR / "results.json")
LASTRUN = str(DATA_DIR / "last_run.txt")

def load_cfg():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.path.exists(SETTINGS):
        st = json.load(open(SETTINGS, encoding="utf-8"))
        for k in ("budget_max", "min_rooms", "allow_furnished", "max_building_age", "attic_needs_elevator",
                  "send_min_score", "digest_min_score", "frequency_minutes", "min_sqm", "notify",
                  "exclude_words", "prefer_words", "rescan_id", "locations"):
            if k in st: cfg[k] = st[k]
        from urllib.parse import urlparse
        for c in st.get("custom_sources", []):
            if c.get("type") == "tg":
                cfg["sources"].append({"name": "TG " + c["name"], "type": "telegram_channel", "channel": c["url"]})
            else:
                u = urlparse(c["url"])
                cfg["sources"].append({"name": c["name"], "type": "listing_site", "url": c["url"],
                                       "link_pattern": "auto", "base": f"{u.scheme}://{u.netloc}"})
    return cfg

# ---------- KAYNAKLAR ----------
def fetch_listing_site(src):
    r = requests.get(src["url"], headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        pat = r"\d{5,}|/ilan" if src["link_pattern"] == "auto" else src["link_pattern"]
        if a["href"].startswith(("javascript", "tel:", "mailto")) or not re.search(pat, a["href"]):
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
    if re.search(r"paylasimli|bodrum kat|\byurt\b|ogrenci|erkek|bayan|ozel oda|odalar mevcut|gunluk|devren|ortak mutfak|ortak oda", t): return None

    # --- eleme kuralları ---
    if price and price > cfg["budget_max"]: return None
    if rooms is not None and rooms < cfg["min_rooms"]: return None
    if not cfg["allow_furnished"] and re.search(r"(?<!es)(?<!un)\besyali\b", t) and "esyasiz" not in t: return None
    if age is not None and age > cfg["max_building_age"]: return None
    attic = bool(re.search(r"cati kat|cati dubleks", t))
    elevator = bool(re.search(r"asansor", t)) and "asansorsuz" not in t and "asansor yok" not in t
    if attic and cfg["attic_needs_elevator"] and not elevator: return None

    sq = re.search(r"(\d{2,3})\s*m(?:²|2)", t)
    sqm = int(sq.group(1)) if sq else None
    if cfg.get("min_sqm") and sqm and sqm < int(cfg["min_sqm"]): return None
    for w in cfg.get("exclude_words", []):
        if fold(w) in t: return None
    pts += min(15, 5 * sum(1 for w in cfg.get("prefer_words", []) if fold(w) in t))
    loc = fold(item.get("loc") or t)
    selected = cfg.get("locations", {})
    city = fold(str(selected.get("city", "")).strip())
    districts = [fold(x.strip()) for x in selected.get("districts", []) if str(x).strip()]
    neighborhoods = [fold(x.strip()) for x in selected.get("neighborhoods", []) if str(x).strip()]
    if city and city not in loc: return None
    if districts and not any(x in loc for x in districts): return None
    if neighborhoods and not any(x in loc for x in neighborhoods): return None
    if not city and not districts and not neighborhoods: return None
    pts += 40
    chosen = [x for x in [city, *districts, *neighborhoods] if x and x in loc]
    notes.append("📍 Seçilen konum: " + ", ".join(chosen))

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
    if item.get("verified"): notes.append("🔎 Detay sayfasından doğrulandı")
    return min(pts, 100), notes, {"price": price, "rooms": rooms, "age": age, "sqm": sqm}

# ---------- DETAY SAYFASI ----------
def parse_detail(raw):
    t = fold(raw)
    extra, loc = [], None
    m = re.search(r"([^,]{3,60}mahallesi),\s*([^,]{3,20}),\s*istanbul\s*-?\s*haritada gor", t)
    if m: loc = " ".join(m.group(1).split()[-3:]) + ", " + m.group(2).strip()
    ag = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})\s*bina yasi", t) or re.search(r"bina yasi\s*(\d{1,2})\s*-\s*(\d{1,2})", t)
    if ag: extra.append(f"{ag.group(2)} yasinda")          # aralığın üst sınırı (temkinli)
    else:
        ag = re.search(r"(\d{1,2})\s*\+?\s*bina yasi", t) or re.search(r"bina yasi\s*(\d{1,2})", t)
        if ag: extra.append(f"{ag.group(1)} yasinda")
    m = re.search(r"asansor\s*(var|yok)", t)
    if m: extra.append("asansor " + m.group(1))
    m = re.search(r"esya durumu\s*(\w+)", t)
    if m: extra.append("esyasiz" if m.group(1) in ("bos", "esyasiz") else "esyali")
    m = re.search(r"([\w\.\-]+(?:\s*kat)?)\s*bulundugu kat", t)
    if m and "bodrum" in m.group(1): extra.append("bodrum kat")
    if m and "cati" in m.group(1): extra.append("cati kati")
    m = re.search(r"(\d{2,3})\s*m²\s*net", t)
    if m: extra.append(f"{m.group(1)} m²")
    return extra, loc

def enrich(it):
    if "emlakjet.com/ilan/" not in it["url"]: return it
    try:
        r = requests.get(it["url"], headers=UA, timeout=30); r.raise_for_status()
        extra, loc = parse_detail(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
    except Exception:
        return it
    return {**it, "text": it["text"] + " | " + " ".join(extra), "loc": loc, "verified": bool(extra or loc)}

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
    prev = json.load(open(RESULTS, encoding="utf-8")) if os.path.exists(RESULTS) else {}
    rescan = bool(cfg.get("rescan_id")) and str(cfg["rescan_id"]) != prev.get("rescan_done", "")
    freq = int(cfg.get("frequency_minutes", 60))
    if not os.environ.get("FORCE") and not rescan and os.path.exists(LASTRUN):
        if time.time() - float(open(LASTRUN).read() or 0) < freq * 60 - 300:
            print("Henüz zamanı gelmedi"); return
    open(LASTRUN, "w").write(str(time.time()))
    out = print if (rescan or not cfg.get("notify", True)) else send
    seen = set() if rescan else (set(json.load(open(STATE))) if os.path.exists(STATE) else set())
    rooms_param = ",".join(f"{n}-1" for n in range(int(cfg["min_rooms"]), 6))
    errors, found = [], []
    for src in cfg["sources"]:
        try:
            if src.get("url"):
                src = {**src, "url": src["url"].replace("{budget_max}", str(cfg["budget_max"])).replace("{rooms}", rooms_param)}
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
    n = int(cfg.get("detail_limit", 25))
    enriched = []
    for sc, nt, it, m in results[:n]:
        it2 = enrich(it); time.sleep(0.7)
        r2 = score(it2, cfg) if it2 is not it else (sc, nt, m)
        if r2: enriched.append((r2[0], r2[1], it2, r2[2]))
    results = sorted(enriched + results[n:], key=lambda x: -x[0])

    for sc, notes, it, _ in [r for r in results if r[0] >= cfg["send_min_score"]]:
        out(f"🏡 Uyum: %{sc}  ({it['source']})\n" + "\n".join(notes) + f"\n{it['url']}")
    mid = [r for r in results if cfg["digest_min_score"] <= r[0] < cfg["send_min_score"]]
    if mid:
        out("📋 Orta uyumlu ilanlar:\n" + "\n".join(f"%{r[0]} – {r[2]['url']}" for r in mid))
    if errors and not found:
        out("⚠️ Hiçbir kaynaktan ilan alınamadı:\n" + "\n".join(errors))

    old = [] if rescan else prev.get("items", [])
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    new = [{"score": sc, "url": it["url"], "source": it["source"], "notes": n, "found": now, **m}
           for sc, n, it, m in results]
    done = str(cfg["rescan_id"]) if rescan else prev.get("rescan_done", "")
    json.dump({"updated": now, "errors": errors, "rescan_done": done, "items": (new + old)[:300]},
              open(RESULTS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(sorted(seen), open(STATE, "w"))

if __name__ == "__main__":
    main()
