#!/usr/bin/env python3
"""
Shops Promo – skaner okazji w sklepach internetowych.

Obsługiwane sklepy (lista i ustawienia w config.json):
- Sinsay, Reserved, House, Cropp  – grupa LPP, wspólny system sklepu: robot używa
  filtra „Tylko produkty przecenione” w każdym dziale,
- Medicine (wearmedicine.com)     – działy „Wyprzedaż”,
- H&M                             – działy „Wyprzedaż”.

Robot:
- zapisuje historię cen w data/history.json (wykrywanie naciąganych rabatów),
- generuje stronę docs/index.html z okazjami.

Nie wymaga dodatkowych bibliotek – wystarczy Python 3.9+.
Uruchomienie:  python scraper.py
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


class Fetch:
    """Proste pobieranie stron z ponawianiem i zapamiętaniem końcowego adresu (przekierowania)."""

    def __init__(self, delay):
        self.delay = delay
        self.sessions = {}

    def browser_get(self, url, warmup=None, tries=2):
        """Połączenie „jak przeglądarka Chrome” (biblioteka curl_cffi) – dla sklepów z ochroną przed botami.
        Bez curl_cffi używa zwykłego pobierania."""
        try:
            from curl_cffi import requests as creq
        except ImportError:
            log("    (brak biblioteki curl_cffi – używam zwykłego połączenia)")
            return self.get(url, referer=warmup)
        host = urllib.parse.urlparse(url).netloc
        s = self.sessions.get(host)
        if s is None:
            s = self.sessions[host] = creq.Session(impersonate="chrome")
            if warmup:
                try:
                    s.get(warmup, timeout=45, headers={"Accept-Language": "pl-PL,pl;q=0.9"})
                    time.sleep(2)
                except Exception as e:
                    log(f"    rozgrzewka nieudana ({e})")
        last = None
        for i in range(tries):
            try:
                r = s.get(url, timeout=45, headers={"Accept-Language": "pl-PL,pl;q=0.9",
                                                    **({"Referer": warmup} if warmup else {})})
                if r.status_code == 200:
                    time.sleep(self.delay)
                    return r.text, str(r.url)
                last = f"HTTP {r.status_code}"
                if r.status_code in (403, 404):
                    break
            except Exception as e:
                last = str(e)
            time.sleep(4 * (i + 1))
        raise RuntimeError(last)

    def get(self, url, referer=None, as_json=False, tries=3):
        headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*" if as_json
            else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.5",
        }
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(referer))
        last = None
        for i in range(tries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=45) as r:
                    body = r.read().decode("utf-8", "replace")
                    final = r.geturl()
                time.sleep(self.delay)
                return (json.loads(body) if as_json else body), final
            except urllib.error.HTTPError as e:
                if e.code in (403, 404, 410):
                    raise
                last = f"HTTP {e.code}"
            except Exception as e:
                last = str(e)
            log(f"    błąd ({last}), ponawiam…")
            time.sleep(4 * (i + 1))
        raise RuntimeError(f"nie udało się pobrać {url}: {last}")


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d\s ]*(?:[.,]\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def json_block(text, start):
    """Wycina z tekstu poprawny blok JSON ([...] lub {...}) zaczynający się w miejscu `start`."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("niekompletny JSON")


# ---------------------------------------------------------------- LPP (Sinsay, Reserved, House, Cropp)

def lpp_resolve_id(f, shop, dept):
    """Gdy sklep zmieni numer działu – odczytaj aktualny ze strony sklepu."""
    html, _ = f.get(shop["site"] + dept["path"])
    m = re.search(r"categoryId:\s*(\d+)", html)
    if m:
        return int(m.group(1))
    # strona działu bywa „okładką” – wejdź w dowolną podkategorię i weź numer działu ze ścieżki
    base = urllib.parse.urlparse(shop["site"]).path + dept["path"] + "/"
    for link in re.findall(r'href="(?:https?://[^/"]+)?(' + re.escape(base) + r'[^"#?]+)"', html)[:5]:
        sub, _ = f.get(urllib.parse.urljoin(shop["site"], link))
        m = re.search(r"categoryPath:\s*'([\d/]+)'", sub)
        if m and len(m.group(1).split("/")) > 2:
            return int(m.group(1).split("/")[2])
    raise RuntimeError("nie znaleziono numeru działu")


def lpp_product(p, shop):
    current = num(p.get("final_price")) or num(p.get("minQtyFinalPrice"))
    regular = num(p.get("price")) or num(p.get("minQtyRegularPrice"))
    img = None
    for getter in (lambda: p["images"]["850"]["front"], lambda: p["firstPhoto"]["sizes"]["medium"],
                   lambda: (p.get("img") or [None])[0]):
        try:
            img = getter()
            if img:
                break
        except Exception:
            pass
    sizes = [str(s["sizeName"]) for s in (p.get("sizes") or [])
             if isinstance(s, dict) and s.get("stock") and s.get("sizeName")]
    return {
        "url": urllib.parse.urljoin(shop["site"], (p.get("url") or "").split("?")[0]),
        "name": (p.get("namePl") or p.get("name") or "").strip(),
        "image": img, "current": current, "regular": regular,
        "lowest30": num(p.get("historyPrice")), "sizes": sizes,
    }


def scan_lpp(f, shop, dept):
    offset, page_size, cat_id, resolved = 0, 200, dept.get("id"), False
    while True:
        url = (f"{shop['api']}{shop['store_id']}/category/{cat_id}/products"
               f"?offset={offset}&pageSize={page_size}&filters[hasDiscount]=1")
        try:
            data, _ = f.get(url, referer=shop["site"], as_json=True)
        except (urllib.error.HTTPError, RuntimeError) as e:
            if offset == 0 and not resolved:
                log(f"    dział nie odpowiada ({e}) – szukam aktualnego numeru")
                cat_id, resolved = lpp_resolve_id(f, shop, dept), True
                continue
            raise
        items = data.get("products") or []
        total = int(data.get("productsTotalAmount") or 0)
        for raw in items:
            yield lpp_product(raw, shop)
        offset += len(items)
        log(f"    {offset}/{total}")
        if not items or offset >= total or offset >= 10000:
            return


# ---------------------------------------------------------------- Medicine (platforma Answear)

def scan_answear(f, shop, dept):
    page, got = 1, 0
    while True:
        url = shop["site"] + dept["path"] + (f"?page={page}" if page > 1 else "")
        html, final = f.get(url)
        i = html.find('"items":[{"url":"/p/')
        if i < 0:
            return
        items = json_block(html, i + 8)
        m = re.search(r'\],"count":(\d+)', html[i:])
        total = int(m.group(1)) if m else 0
        for p in items:
            img = None
            try:
                mi = p["productImages"]["mainImage"]
                img = f"https://img2.ans-media.com/i/400x600/{mi['name']}?v={mi.get('version', '')}"
            except Exception:
                pass
            sizes = [s["name"] for s in (p.get("allSizes") or [])
                     if s.get("name") and "OUT" not in str((s.get("variation") or {}).get("availability", "")).upper()]
            yield {
                "url": urllib.parse.urljoin(shop["site"], p.get("url") or ""),
                "name": (p.get("frontName") or p.get("name") or "").strip(),
                "image": img, "current": num(p.get("price")), "regular": num(p.get("priceRegular")),
                "lowest30": num(p.get("priceMinimal")), "sizes": sizes,
            }
        got += len(items)
        log(f"    {got}/{total}")
        if not items or got >= total or page >= 200:
            return
        page += 1


# ---------------------------------------------------------------- H&M

def scan_hm(f, shop, dept):
    page = 1
    while True:
        url = shop["site"] + dept["path"] + (f"?page={page}" if page > 1 else "")
        html, final = f.browser_get(url, warmup=shop["site"] + "/pl_pl/index.html")
        if urllib.parse.urlparse(final).path != dept["path"]:
            log("    brak działu wyprzedaży (sklep przekierował na inną stronę)")
            return
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            raise RuntimeError("nie znaleziono danych produktów na stronie")
        d = (json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("plpProps", {})
             .get("productListingSectionProps", {}).get("productListingData", {}))
        hits = d.get("hits") or []
        for p in hits:
            prices = {x.get("priceType"): num(x.get("price")) for x in (p.get("prices") or [])}
            current = prices.get("redPrice") or num(p.get("redPrice")) or prices.get("whitePrice")
            regular = prices.get("whitePrice") or num(p.get("regularPrice"))
            yield {
                "url": urllib.parse.urljoin(shop["site"], p.get("pdpUrl") or ""),
                "name": (p.get("title") or "").strip(),
                "image": p.get("imageProductSrc") or p.get("imageModelSrc"),
                "current": current, "regular": regular,
                "lowest30": num(p.get("priorPrice")), "sizes": [],
            }
        pages = int(d.get("totalPages") or 1)
        log(f"    strona {page}/{pages}")
        if not hits or page >= pages or page >= 100:
            return
        page += 1


SCANNERS = {"lpp": scan_lpp, "answear": scan_answear, "hm": scan_hm}


def scan(cfg):
    f = Fetch(cfg["delay_seconds"])
    products, per_shop = {}, {}
    for shop in cfg["shops"]:
        if not shop.get("enabled", True):
            continue
        log(f"=== {shop['name']} ===")
        count = 0
        for dept in shop["departments"]:
            log(f"  {dept['label']}")
            try:
                for p in SCANNERS[shop["type"]](f, shop, dept):
                    if not p["url"] or not p["name"] or not p["current"] or not p["regular"]:
                        continue
                    p.update(shop=shop["name"], category=dept["label"],
                             current=round(p["current"], 2), regular=round(p["regular"], 2),
                             sizes=p.get("sizes", [])[:14])
                    if p["url"] not in products:
                        products[p["url"]] = p
                        count += 1
            except Exception as e:
                log(f"    POMIJAM ({e})")
        per_shop[shop["name"]] = count
        log(f"  {shop['name']}: {count} przecenionych produktów")
    return list(products.values()), per_shop


# ---------------------------------------------------------------- historia cen i strona

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
    last_scan = hist.get("_last_scan")

    deals = []
    for p in products:
        discount = round((1 - p["current"] / p["regular"]) * 100) if p["regular"] > p["current"] else 0
        if discount < cfg["min_discount_percent"]:
            continue
        h = hist.setdefault(p["url"], {"p": []})
        prev = [x for x in h["p"] if x[0] >= cutoff]
        min_before = min((x[1] for x in prev), default=None)
        last = prev[-1] if prev else None
        if not (h.get("ls") == last_scan and h.get("since")):
            h["since"] = now_s
        low = p.get("lowest30")
        deals.append({
            **p,
            "discount": discount,
            "sale_since": h["since"],
            "price_drop": bool(last and p["current"] < last[1]),
            "was_cheaper": bool(min_before is not None and min_before < p["current"] - 0.01),
            "min_seen": min(min_before, p["current"]) if min_before is not None else p["current"],
            "real_discount30": round((1 - p["current"] / low) * 100) if low else None,
        })
        if not last or last[1:] != [p["current"], p["regular"]]:
            prev.append([now_s, p["current"], p["regular"]])
        h["p"], h["ls"] = prev, now_s

    hist["_last_scan"] = now_s
    hist = {k: v for k, v in hist.items()
            if k == "_last_scan" or (isinstance(v, dict) and v.get("ls", "") >= cutoff)}
    DATA.mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return deals


def build_page(deals, per_shop):
    DOCS.mkdir(exist_ok=True)
    # sklep chwilowo niedostępny? zostaw jego poprzednie okazje zamiast je kasować
    prev = load_json(DOCS / "deals.json", {}).get("deals", [])
    for shop, count in per_shop.items():
        if count == 0:
            old = [dict(d, stale=True) for d in prev if d.get("shop") == shop]
            if old:
                log(f"  {shop}: brak nowych danych – zostawiam {len(old)} okazji z poprzedniego skanu")
                deals = deals + old
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shops": per_shop,
        "deals": sorted(deals, key=lambda d: -d["discount"]),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (DOCS / "deals.json").write_text(raw, encoding="utf-8")
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    (DOCS / "index.html").write_text(tpl.replace("/*__DATA__*/null", raw.replace("</", "<\\/")),
                                     encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--debug", action="store_true", help="(zostawione dla zgodności, nic nie robi)")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    products, per_shop = scan(cfg)
    log("Podsumowanie: " + ", ".join(f"{k}: {v}" for k, v in per_shop.items()))
    if not products:
        log("BŁĄD: nie pobrano żadnych produktów – strona NIE została nadpisana.")
        sys.exit(1)
    deals = update_history(products, cfg)
    build_page(deals, per_shop)
    log(f"Gotowe: {len(deals)} okazji (rabat ≥ {cfg['min_discount_percent']}%) zapisano w docs/index.html")


if __name__ == "__main__":
    main()
