#!/usr/bin/env python3
"""
Shops Promo – skaner okazji (na razie Sinsay).

1. Otwiera sinsay.com w przeglądarce bez okna (Playwright / Chromium).
2. Znajduje działy wyprzedaży/promocji (albo używa adresów z config.json).
3. Przewija każdą listę produktów i zbiera: nazwę, link, zdjęcie, cenę
   obecną, cenę regularną i (jeśli jest) najniższą cenę z 30 dni.
4. Zapisuje historię cen w data/history.json, żeby wykrywać sztuczne rabaty.
5. Generuje stronę docs/index.html z samymi przecenionymi produktami.

Uruchomienie:  python scraper.py            (normalny skan)
               python scraper.py --debug    (zapisuje zrzuty ekranu i HTML w debug/)
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
DEBUG = ROOT / "debug"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#cookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "button:has-text('Akceptuj wszystkie')",
    "button:has-text('Zaakceptuj wszystkie')",
    "button:has-text('Akceptuję')",
    "button:has-text('Akceptuj')",
    "button:has-text('Zgadzam się')",
    "button:has-text('Accept all')",
]
LOAD_MORE_RE = re.compile(r"(pokaż|załaduj|wczytaj|zobacz)\s+(więcej|kolejne)", re.I)

# Kod JS uruchamiany na stronie listy produktów: znajduje kafelki produktów
# niezależnie od nazw klas CSS (szuka linków do produktów + cen "xx,xx zł").
EXTRACT_JS = r"""
(productRe) => {
  const prodRe = new RegExp(productRe, 'i');
  const priceRe = /(\d{1,5}(?:[  ]\d{3})*(?:[.,]\d{1,2})?)\s*(?:zł|PLN)/gi;
  const toNum = s => parseFloat(s.replace(/[  ]/g, '').replace(',', '.'));
  const clean = h => { try { const u = new URL(h, location.href); return u.origin + u.pathname; } catch(e) { return null; } };
  const isProd = h => { try { return prodRe.test(new URL(h, location.href).pathname); } catch(e) { return false; } };

  const prodLinks = el => {
    const s = new Set();
    el.querySelectorAll('a[href]').forEach(a => { if (isProd(a.href)) s.add(clean(a.href)); });
    return s;
  };

  const results = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    if (!isProd(a.href)) continue;
    const url = clean(a.href);
    if (results.has(url)) continue;
    // Wspinamy się w górę drzewa, dopóki kontener dotyczy tylko jednego produktu.
    let tile = a, el = a.parentElement;
    for (let i = 0; i < 8 && el && el !== document.body; i++, el = el.parentElement) {
      if (prodLinks(el).size > 1) break;
      tile = el;
    }
    const text = tile.innerText || '';
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);

    let lowest30 = null; const prices = [];
    lines.forEach((line, i) => {
      const m = [...line.matchAll(priceRe)].map(x => toNum(x[1]));
      if (!m.length) return;
      const ctx = line + ' ' + (lines[i-1] || '');
      if (/najni|30\s*dni|omnibus/i.test(ctx)) lowest30 = Math.min(...m);
      else prices.push(...m);
    });
    if (!prices.length) continue;

    // Cena przekreślona = cena regularna (jeśli da się ją wykryć stylem).
    let struck = null;
    tile.querySelectorAll('*').forEach(e => {
      if (e.children.length > 2) return;
      const st = getComputedStyle(e);
      if (e.tagName === 'S' || e.tagName === 'DEL' || (st.textDecorationLine || '').includes('line-through')) {
        const m = [...(e.innerText || '').matchAll(priceRe)];
        if (m.length) struck = toNum(m[0][1]);
      }
    });

    const current = Math.min(...prices);
    const regular = struck && struck > current ? struck : Math.max(...prices);

    const img = tile.querySelector('img');
    let image = null;
    if (img) {
      image = img.currentSrc || img.src || img.dataset.src || null;
      if ((!image || image.startsWith('data:')) && img.srcset) image = img.srcset.split(',').pop().trim().split(' ')[0];
      if (image && image.startsWith('data:')) image = null;
    }
    let name = (img && img.alt) || a.title || a.getAttribute('aria-label') || '';
    if (!name) name = lines.find(l => !/\d\s*(zł|PLN)|^-?\d+\s*%$/i.test(l) && l.length > 3) || '';
    results.set(url, { url, name: name.trim(), image, current, regular, lowest30 });
  }
  return [...results.values()];
}
"""

DISCOVER_JS = r"""
(args) => {
  const [keywords, host] = args;
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    let u; try { u = new URL(a.href, location.href); } catch(e) { continue; }
    if (u.host !== host) continue;
    const label = ((a.innerText || '') + ' ' + (a.getAttribute('aria-label') || '')).trim().replace(/\s+/g, ' ');
    const hay = (label + ' ' + u.pathname).toLowerCase();
    if (keywords.some(k => hay.includes(k))) out.push({ url: u.origin + u.pathname + u.search, label: label.slice(0, 60) });
  }
  return out;
}
"""


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def accept_cookies(page):
    for sel in COOKIE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=2000)
                log("  zaakceptowano cookies")
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def save_debug(page, name):
    DEBUG.mkdir(exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "_", name.lower())[:60]
    try:
        page.screenshot(path=str(DEBUG / f"{safe}.png"), full_page=False)
        (DEBUG / f"{safe}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        log("  nie udało się zapisać debug:", e)


def label_from_url(url):
    parts = [p for p in urlparse(url).path.split("/") if p and p not in ("pl",)]
    return " / ".join(p.replace("-", " ") for p in parts[-2:]) or "Wyprzedaż"


def scrape_listing(page, url, label, cfg, debug):
    log(f"Lista: {label}  ({url})")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except PWTimeout:
        log("  timeout przy ładowaniu, pomijam")
        return []
    page.wait_for_timeout(2500)
    accept_cookies(page)

    found, stable = {}, 0
    for i in range(cfg["max_scrolls_per_page"]):
        for p in page.evaluate(EXTRACT_JS, cfg["product_url_regex"]):
            found[p["url"]] = p
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(900)
        # przycisk "Pokaż więcej"
        try:
            more = page.get_by_role("button", name=LOAD_MORE_RE).first
            if more.is_visible(timeout=300):
                more.click(timeout=2000)
                page.wait_for_timeout(1500)
        except Exception:
            pass
        before = len(found)
        for p in page.evaluate(EXTRACT_JS, cfg["product_url_regex"]):
            found[p["url"]] = p
        stable = stable + 1 if len(found) == before else 0
        at_bottom = page.evaluate("window.innerHeight + window.scrollY >= document.body.scrollHeight - 50")
        if stable >= 4 and at_bottom:
            break
    if debug or not found:
        save_debug(page, label or url)
    for p in found.values():
        p["category"] = label
    log(f"  produktów: {len(found)}")
    return list(found.values())


def scan(cfg, debug=False):
    base = cfg["base_url"]
    host = urlparse(base).netloc
    products = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="pl-PL", user_agent=UA, viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        listings = [{"url": u, "label": label_from_url(u)} for u in cfg.get("start_urls", [])]
        if not listings:
            log("Szukam działów wyprzedaży na", base)
            page.goto(base, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            accept_cookies(page)
            if debug:
                save_debug(page, "strona_glowna")
            seen = set()
            for l in page.evaluate(DISCOVER_JS, [cfg["discover_keywords"], host]):
                key = l["url"].rstrip("/")
                if key in seen or re.search(cfg["product_url_regex"], urlparse(key).path, re.I):
                    continue
                seen.add(key)
                listings.append({"url": l["url"], "label": l["label"] or label_from_url(l["url"])})
            listings = listings[: cfg["max_listing_pages"]]
            log(f"Znaleziono działów: {len(listings)}")
            for l in listings:
                log("  -", l["label"], l["url"])
            if not listings:
                save_debug(page, "brak_dzialow")

        for l in listings:
            for p in scrape_listing(page, l["url"], l["label"], cfg, debug):
                old = products.get(p["url"])
                if not old or (p["regular"] - p["current"]) > (old["regular"] - old["current"]):
                    products[p["url"]] = p
            time.sleep(cfg["delay_seconds"])
        browser.close()
    return list(products.values())


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def update_history(products, cfg):
    hist_path = DATA / "history.json"
    hist = load_json(hist_path, {})
    now = datetime.now(timezone.utc)
    now_s = now.isoformat(timespec="seconds")
    cutoff = (now - timedelta(days=cfg["history_days"])).isoformat()

    deals = []
    for p in products:
        if not p["name"] or p["current"] <= 0:
            continue
        h = hist.setdefault(p["url"], {"first_seen": now_s, "prices": []})
        prev = [x for x in h["prices"] if x[0] >= cutoff]
        was_on_sale_before = any(x[1] < x[2] for x in prev)
        min_before = min((x[1] for x in prev), default=None)
        last_price = prev[-1][1] if prev else None

        discount = round((1 - p["current"] / p["regular"]) * 100) if p["regular"] > p["current"] else 0
        if discount >= cfg["min_discount_percent"]:
            if not (was_on_sale_before and h.get("sale_since")):
                h["sale_since"] = now_s
            deals.append({
                **p,
                "discount": discount,
                "sale_since": h["sale_since"],
                "price_drop": bool(last_price and p["current"] < last_price),
                "was_cheaper": bool(min_before is not None and min_before < p["current"]),
                "min_seen": min(min_before, p["current"]) if min_before is not None else p["current"],
                "real_discount30": (round((1 - p["current"] / p["lowest30"]) * 100)
                                    if p.get("lowest30") and p["lowest30"] > 0 else None),
            })
        else:
            h.pop("sale_since", None)

        h.update(name=p["name"], image=p["image"], category=p["category"], last_seen=now_s)
        if not prev or prev[-1][1:] != [p["current"], p["regular"]]:
            prev.append([now_s, p["current"], p["regular"]])
        h["prices"] = prev

    # usuń produkty niewidziane od dawna
    hist = {k: v for k, v in hist.items() if v.get("last_seen", "") >= cutoff}
    DATA.mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=0), encoding="utf-8")
    return deals


def build_page(deals, total_scanned):
    DOCS.mkdir(exist_ok=True)
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanned": total_scanned,
        "deals": sorted(deals, key=lambda d: -d["discount"]),
    }
    (DOCS / "deals.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (DOCS / "index.html").write_text(tpl.replace("/*__DATA__*/null", data), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true", help="zapisz zrzuty ekranu i HTML do debug/")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    products = scan(cfg, args.debug)
    log(f"Łącznie produktów: {len(products)}")
    if not products:
        log("BŁĄD: nie znaleziono żadnych produktów — strona NIE została nadpisana. "
            "Sprawdź pliki w folderze debug/ (zrzut ekranu pokaże, co widział robot).")
        sys.exit(1)
    deals = update_history(products, cfg)
    build_page(deals, len(products))
    log(f"Gotowe: {len(deals)} okazji zapisano w docs/index.html")


if __name__ == "__main__":
    main()
