#!/usr/bin/env python3
"""
Shops Promo – skaner okazji w sklepach internetowych.

Obsługiwane sklepy (lista i ustawienia w config.json):
- Sinsay, Reserved, House, Cropp  – grupa LPP, wspólny system sklepu: robot używa
  filtra „Tylko produkty przecenione” w każdym dziale,
- Mohito                          – jak wyżej (grupa LPP),
- Medicine (wearmedicine.com)     – działy „Wyprzedaż”,
- H&M                             – działy „Wyprzedaż”,
- Esotiq, Calzedonia, Intimissimi  – działy „Wyprzedaż”,
- Zara, Bershka, Pull&Bear, Stradivarius – działy „Promocje” / „Special prices” / „Wyprzedaż”
  wyszukiwane automatycznie w drzewie kategorii sklepu przy każdym skanie.

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
    import threading
    name = threading.current_thread().name
    prefix = [f"[{name}]"] if not name.startswith(("Main", "Thread")) else []
    # jeden napis naraz – przy równoległych wątkach linie logu się nie przeplatają
    print(" ".join(map(str, [datetime.now().strftime("%H:%M:%S"), *prefix, *a])), flush=True)


class Fetch:
    """Proste pobieranie stron z ponawianiem i zapamiętaniem końcowego adresu (przekierowania)."""

    def __init__(self, delay):
        self.delay = delay
        self.sessions = {}

    def browser_get(self, url, warmup=None, tries=2, headers=None):
        """Połączenie „jak przeglądarka Chrome” (biblioteka curl_cffi) – dla sklepów z ochroną przed botami.
        Bez curl_cffi używa zwykłego pobierania."""
        try:
            from curl_cffi import requests as creq
        except ImportError:
            log("    (brak biblioteki curl_cffi – używam zwykłego połączenia)")
            return self.get(url, referer=warmup, headers=headers)
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
                                                    **({"Referer": warmup} if warmup else {}), **(headers or {})})
                if r.status_code == 200:
                    time.sleep(self.delay)
                    return r.text, str(r.url)
                last = f"HTTP {r.status_code}"
                if r.status_code in (403, 404):
                    break
            except Exception as e:
                last = str(e)
            time.sleep(4 * (i + 1))
        if last in ("HTTP 403", "HTTP 407"):
            # część sklepów (np. CCC) odrzuca połączenie „jak Chrome”, a zwykłe przepuszcza
            log(f"    {last} – próbuję zwykłego połączenia")
            return self.get(url, referer=warmup, headers=headers, tries=1)
        raise RuntimeError(last)

    def get(self, url, referer=None, as_json=False, tries=3, headers=None):
        extra, headers = headers, {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*" if as_json
            else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.5",
        }
        headers.update(extra or {})
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


KIDS_RE = re.compile(r"\b(dziec\w*|dla dzieci|niemowl\w*|chłopi\w*|dziewczyn\w*|junior\w*|kids?|baby|toddler|"
                     r"paw patrol|psi patrol)\b", re.I)
SHOE_RE = re.compile(r"\b(buty|obuwie|sneaker\w*|trampk\w*|tenisówk\w*|botk\w*|botek|kozak\w*|kozaczk\w*|"
                     r"półbut\w*|sandał\w*|sandałk\w*|klapk\w*|japonk\w*|kapc\w*|czółenk\w*|szpilk\w*|mokasyn\w*|"
                     r"baleri\w*|śniegow\w*|lords\w*|loafers\w*|espadryl\w*|oficerk\w*|sztyblet\w*|kalosz\w*|"
                     r"chodak\w*|trapery|glany|derby|oxfordy|slip-?on\w*|muły|shoes?|sneakers?|boots?|sandals?)\b", re.I)
BEST_RE = re.compile(r"best\s*-?\s*seller|top\s*seller|hit\s+sprzeda", re.I)


def is_best(raw):
    """Czy sklep oznaczył produkt jako bestseller? Sprawdza tylko wartości tekstowe
    (nie nazwy pól), żeby pole w rodzaju "bestseller": null nie dawało fałszywego wyniku."""
    if isinstance(raw, str):
        return bool(BEST_RE.search(raw))
    found = False

    def walk(o):
        nonlocal found
        if found:
            return
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and len(o) < 80 and BEST_RE.search(o):
            found = True
    walk(raw)
    return found


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
    # rozmiar jest do kupienia, gdy sklep podaje stock=true i (jeśli podaje) liczbę sztuk > 0
    sizes = [str(s["sizeName"]) for s in (p.get("sizes") or [])
             if isinstance(s, dict) and s.get("stock") and s.get("sizeName")
             and (s.get("stockQuantity") is None or (num(s.get("stockQuantity")) or 0) > 0)]
    return {
        "url": urllib.parse.urljoin(shop["site"], (p.get("url") or "").split("?")[0]),
        "name": (p.get("namePl") or p.get("name") or "").strip(),
        "image": img, "current": current, "regular": regular,
        "lowest30": num(p.get("historyPrice")), "sizes": sizes,
        # sklep oznacza bestsellery polem "bestseller" (np. „bestseller w kategorii X”) albo naklejką BESTSELLER
        "best": bool(p.get("bestseller")) or is_best(p.get("stickers")) or is_best(p.get("sticker")),
        "available": bool(sizes) if p.get("sizes") else True,
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
                "best": is_best(p.get("badges")) or is_best(p.get("badgesFront")),
                "available": p.get("availableForBuy", True) is not False and (bool(sizes) or not p.get("allSizes")),
            }
        got += len(items)
        log(f"    {got}/{total}")
        if not items or got >= total or page >= 200:
            return
        page += 1


# ---------------------------------------------------------------- H&M

def scan_hm(f, shop, dept):
    page, seen = 1, set()
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
        new = [p for p in hits if p.get("pdpUrl") and p["pdpUrl"] not in seen]
        seen.update(p["pdpUrl"] for p in new)
        for p in new:
            prices = {x.get("priceType"): num(x.get("price")) for x in (p.get("prices") or [])}
            current = prices.get("redPrice") or num(p.get("redPrice")) or prices.get("whitePrice")
            regular = prices.get("whitePrice") or num(p.get("regularPrice"))
            yield {
                "url": urllib.parse.urljoin(shop["site"], p.get("pdpUrl") or ""),
                "name": (p.get("title") or "").strip(),
                "image": p.get("imageProductSrc") or p.get("imageModelSrc"),
                "current": current, "regular": regular,
                "lowest30": num(p.get("priorPrice")), "sizes": [],
                "best": is_best(p.get("marketingMarkerText") or "") or is_best(p.get("promotionalMarkerText") or ""),
            }
        total = int(d.get("totalHits") or 0)
        pages = int(d.get("totalPages") or 0)
        log(f"    strona {page}" + (f"/{pages}" if pages else "") + f" – {len(seen)}" + (f"/{total}" if total else "") + " produktów")
        # następna strona, dopóki przybywa nowych produktów (H&M nie zawsze podaje liczbę stron)
        if page == 1:
            first_size = len(hits)
        if not new or len(hits) < first_size or (pages and page >= pages) or (total and len(seen) >= total) or page >= 100:
            return
        page += 1


# ---------------------------------------------------------------- Inditex: wspólne

SECTION_NAMES = {"KOBIETA": "Kobieta", "WOMAN": "Kobieta", "MĘŻCZYZNA": "Mężczyzna", "MAN": "Mężczyzna",
                 "DZIECI": "Dziecko", "KIDS": "Dziecko", "ZARA HOME": "Dom", "HOME": "Dom", "BEAUTY": "Uroda"}
PROMO_KEY = re.compile(r"(?:^|[_-])(PROMO|PROMOS|PROMOCIONES|PVP_GENERAL|SPECIAL[_-]?PRICES|REBAJAS|SALE)(?:[_-]|$)", re.I)


SKIP_DEPARTMENTS = set()   # ustawiane z config.json ("skip_departments")


def section_label(path):
    top = path.strip("/").split("/")[0].strip().upper()
    return SECTION_NAMES.get(top, top.title() or "Inne")


BEST_CAT = re.compile(r"BEST[ _-]?SELLER", re.I)


def find_categories(cats, match):
    """Kategorie z drzewa sklepu spełniające `match(key, name)` – dobierane przy każdym skanie,
    więc sezonowe wyprzedaże i nowe działy bestsellerów wchodzą automatycznie."""
    found, seen = [], set()

    def walk(items, path):
        for c in items or []:
            p = path + "/" + (c.get("name") or "")
            key = str(c.get("key") or "")
            if match(key, c.get("name") or "") and not re.search(r"TEST|GIFT", key, re.I) \
                    and section_label(p) not in SKIP_DEPARTMENTS:
                cid = c.get("redirectCategoryId") or c.get("id")
                if cid and cid not in seen:
                    seen.add(cid)
                    found.append((cid, section_label(p), p))
            walk(c.get("subcategories"), p)
    walk(cats, "")
    return found


def promo_categories(cats):
    return find_categories(cats, lambda key, name: PROMO_KEY.search(key))


def best_categories(cats):
    return find_categories(cats, lambda key, name: BEST_CAT.search(key + " " + name))


def money(v):
    n = num(v)
    return round(n / 100, 2) if n is not None else None


# ---------------------------------------------------------------- Zara

def scan_zara(f, shop):
    site = shop["site"]
    warm = site + "/pl/"
    text, _ = f.browser_get(site + "/pl/pl/categories?ajax=true", warmup=warm)
    tree = json.loads(text).get("categories")
    cats = [(c, False) for c in promo_categories(tree)] + [(c, True) for c in best_categories(tree)]
    log(f"  działy promocji: {sum(1 for c in cats if not c[1])}, bestsellerów: {sum(1 for c in cats if c[1])}")
    for (cid, label, path), in_best in cats:
        try:
            text, _ = f.browser_get(f"{site}/pl/pl/category/{cid}/products?ajax=true", warmup=warm)
            data = json.loads(text)
        except Exception as e:
            log(f"    {path}: pomijam ({e})")
            continue
        n = 0
        for g in data.get("productGroups") or []:
            for el in g.get("elements") or []:
                for c in el.get("commercialComponents") or []:
                    if not c.get("oldPrice") or not c.get("seo"):
                        continue
                    seo = c["seo"]
                    img = None
                    try:
                        x = c["detail"]["colors"][0]["xmedia"][0]
                        img = (x.get("url") or "").replace("{width}", "500") or x["extraInfo"]["deliveryUrl"]
                    except Exception:
                        pass
                    n += 1
                    yield label, {
                        "url": f"{site}/pl/pl/{seo.get('keyword')}-p{seo.get('seoProductId')}.html?v1={seo.get('discernProductId')}",
                        "name": (c.get("name") or "").strip().capitalize(),
                        "image": img, "current": money(c.get("price")), "regular": money(c.get("oldPrice")),
                        "lowest30": None, "sizes": [],
                        "best": in_best or is_best(c.get("productTag")) or is_best(c.get("tagTypes")),
                        "available": str(c.get("availability") or "in_stock").lower() == "in_stock",
                    }
        log(f"    {path}: {n}")


# ---------------------------------------------------------------- Bershka, Pull&Bear, Stradivarius

def itx_image(detail):
    """Zdjęcie produktu. Pierwsze pliki na liście bywają zbliżeniem materiału (clazz 2) –
    bierzemy pierwsze zwykłe zdjęcie (clazz 1) i prosimy o mniejszy rozmiar."""
    fallback = None
    for x in detail.get("xmedia") or []:
        for item in x.get("xmediaItems") or []:
            for m in item.get("medias") or []:
                url = (m.get("extraInfo") or {}).get("deliveryUrl")
                if not url:
                    continue
                url += ("&" if "?" in url else "?") + "w=500&f=auto"
                if m.get("clazz") == 1:
                    return url
                fallback = fallback or url
    return fallback


def scan_itx(f, shop):
    site, S, C, L = shop["site"], shop["store_id"], shop["catalog_id"], shop.get("lang", -22)
    base = f"{site}/itxrest"
    warm = site + "/pl/"
    q = f"languageId={L}&appId=1"
    text, _ = f.browser_get(f"{base}/2/catalog/store/{S}/{C}/category?{q}&typeCatalog=1", warmup=warm)
    tree = json.loads(text).get("categories")
    cats = [(c, False) for c in promo_categories(tree)] + [(c, True) for c in best_categories(tree)]
    log(f"  działy promocji: {sum(1 for c in cats if not c[1])}, bestsellerów: {sum(1 for c in cats if c[1])}")
    labels, best_ids = {}, set()
    for (cid, label, path), in_best in cats:
        try:
            text, _ = f.browser_get(f"{base}/3/catalog/store/{S}/{C}/category/{cid}/product?{q}&showProducts=false",
                                    warmup=warm)
            ids = json.loads(text).get("productIds") or []
        except Exception as e:
            log(f"    {path}: pomijam ({e})")
            continue
        log(f"    {path}: {len(ids)}")
        for i in ids:
            labels.setdefault(i, label)
            if in_best:
                best_ids.add(i)
    ids = list(labels)
    for k in range(0, len(ids), 40):
        chunk = ids[k:k + 40]
        try:
            text, _ = f.browser_get(f"{base}/3/catalog/store/{S}/{C}/productsArray?productIds="
                                    + ",".join(map(str, chunk)) + f"&{q}", warmup=warm)
            products = json.loads(text).get("products") or []
        except Exception as e:
            log(f"    produkty {k}-{k + len(chunk)}: pomijam ({e})")
            continue
        for p in products:
            summ = (p.get("bundleProductSummaries") or [p])[0]
            detail = summ.get("detail") or p.get("detail") or {}
            colors = detail.get("colors") or []
            sizes = (colors[0].get("sizes") if colors else None) or []
            priced = [s for s in sizes if s.get("price")]
            if not priced:
                continue
            best = min(priced, key=lambda s: num(s["price"]) or 1e9)
            img = itx_image(detail)
            purl = p.get("productUrl") or summ.get("productUrl")
            if not purl:
                continue
            yield labels.get(p.get("id"), "Inne"), {
                "url": f"{site}/pl/{purl}-c0p{p.get('id')}.html",
                "name": (p.get("name") or "").strip(),
                "image": img, "current": money(best.get("price")), "regular": money(best.get("oldPrice")),
                "lowest30": None,
                "sizes": [str(s.get("name")) for s in sizes if s.get("isBuyable") and s.get("name")],
                "best": p.get("id") in best_ids or is_best(p.get("tags")),
                "available": p.get("isBuyable", True) is not False and any(s.get("isBuyable") for s in sizes),
            }
        log(f"    produkty: {min(k + 40, len(ids))}/{len(ids)}")


# ---------------------------------------------------------------- Esotiq

ESOTIQ_BEST = {}


def scan_esotiq(f, shop, dept):
    # etykiety produktów są podane numerami – słownik numer → tekst pobieramy raz
    if not ESOTIQ_BEST:
        try:
            labels, _ = f.get(shop["api"] + "labels/", referer=shop["site"], as_json=True)
            ESOTIQ_BEST.update({str(l["id"]): True for l in labels
                                if is_best(re.sub(r"<[^>]+>", " ", l.get("text") or ""))})
        except Exception as e:
            log(f"    nie udało się pobrać etykiet ({e})")
        ESOTIQ_BEST.setdefault("_", False)
    url = f"{shop['api']}model-colors/?category={dept['id']}&page=1&page_size=100"
    got = 0
    while url:
        data, _ = f.get(url, referer=shop["site"], as_json=True)
        items = data.get("results") or []
        for p in items:
            pc = p.get("price_collection") or {}
            img = (p.get("photo") or {}).get("image")
            yield {
                "url": shop["site"] + (p.get("slug") or ""),
                "name": (p.get("name") or "").strip(),
                "image": img, "current": num(pc.get("price")),
                "regular": num(pc.get("base_price")) or num(pc.get("reference_price")),
                "lowest30": num(pc.get("min_30d_price")), "sizes": [],
                "best": any(ESOTIQ_BEST.get(str(l)) for l in [*(p.get("labels") or []), p.get("offer_label")]),
            }
        got += len(items)
        log(f"    {got}/{data.get('count')}")
        url = data.get("next") if items and got < 5000 else None


# ---------------------------------------------------------------- Calzedonia (nowa wersja sklepu Salesforce)

def public_image(url, site):
    """Calzedonia podaje zdjęcia z wewnętrznego serwera (…demandware.net), który blokuje
    dostęp z zewnątrz – ten sam plik jest dostępny pod adresem sklepu."""
    if not url:
        return None
    u = urllib.parse.urlparse(url)
    if u.netloc.endswith("demandware.net"):
        return site.rstrip("/") + u.path + ("?" + u.query if u.query else "")
    return url


def scan_calz(f, shop, dept):
    page, got, seen = 1, 0, set()
    while True:
        url = shop["site"] + dept["path"] + (f"?page={page}" if page > 1 else "")
        html, _ = f.browser_get(url, warmup=shop["site"] + "/pl/")
        k = html.find('"c_price"')
        start = html.rfind('"hits":[', 0, k) if k > 0 else -1
        if start < 0:
            log("    brak produktów na stronie")
            return
        hits = json_block(html, start + 7)
        m = re.search(r'"total":(\d+)', html[start:start + 3_000_000])
        total = int(m.group(1)) if m else 0
        new = 0
        for p in hits:
            path = p.get("c_seoUrlPathname") or ""
            if not path or path in seen:
                continue
            seen.add(path)
            new += 1
            pr = p.get("c_price") or {}
            sales, lst = pr.get("sales") or {}, pr.get("list") or {}
            sizes = []
            for va in p.get("c_variationsFullData") or []:
                if va.get("attributeId") == "size":
                    sizes = [v.get("displayValue") for v in va.get("values") or []
                             if v.get("displayValue") and not v.get("isSoldout")]
            yield {
                "url": shop["site"] + "/pl" + path,
                "name": (p.get("productName") or "").strip(),
                "image": public_image(p.get("c_image"), shop["site"]),
                "current": num(sales.get("value")), "regular": num(lst.get("value")),
                "lowest30": num((sales.get("oldPriceOmnibus") or {}).get("value")), "sizes": sizes,
                "best": is_best({k: v for k, v in p.items() if k not in ("productName", "c_variationsFullData")}),
                "available": not p.get("c_isSoldout") and not p.get("c_comingSoon"),
            }
        got += new
        log(f"    strona {page} – {got}/{total}")
        if not new or (total and got >= total) or page >= 100:
            return
        page += 1


# ---------------------------------------------------------------- Intimissimi (klasyczna wersja sklepu Salesforce)

def scan_sfra(f, shop, dept):
    import html as htmllib
    start, seen = 0, set()
    while True:
        url = shop["site"] + dept["path"] + f"?start={start}&sz=200"
        page, _ = f.browser_get(url, warmup=shop["site"] + "/pl/")
        tiles = page.split('class="cc-prodTile js-prodTile')[1:]
        new = 0
        for t in tiles:
            m = re.search(r'class="js-prodTile-name"\s+href="([^"]+)"', t) or re.search(r'href="(/[^"]*/product/[^"]+)"', t)
            if not m:
                continue
            href = htmllib.unescape(m.group(1))
            if href in seen:
                continue
            seen.add(href)
            new += 1
            name = re.search(r'itemprop="name">\s*([^<]+?)\s*<', t)
            img = re.search(r'<img[^>]+src="([^"]+)"', t)
            pi = t.find('class="price"')
            block = t[pi:pi + 4000] if pi >= 0 else ""
            vals = {float(x) for x in re.findall(r'content="(\d+(?:\.\d+)?)"', block)}
            vals |= {num(x) for x in re.findall(r"(\d+,\d{2})\s*zł", block)}
            vals = sorted(v for v in vals if v)
            if not vals:
                continue
            yield {
                "url": urllib.parse.urljoin(shop["site"], href),
                "name": htmllib.unescape(name.group(1)) if name else "",
                "image": htmllib.unescape(img.group(1)) if img else None,
                "current": vals[0], "regular": vals[-1], "lowest30": None, "sizes": [],
                "best": is_best(re.sub(r"<[^>]+>", " ", t)),
            }
        log(f"    {len(seen)} produktów")
        if not new or len(tiles) < 150 or start >= 2000:
            return
        start += 200


# ---------------------------------------------------------------- CCC

def scan_ccc(f, shop, dept):
    fa = urllib.parse.quote('{"filterAttributeOptions":[]}')
    # nagłówki, które dokłada strona CCC do każdego zapytania (bez nich API odpowiada 400)
    headers = {"x-api-version": "4.0", "x-frontend-id": "44", "x-source-name": "WWW"}
    page = 1
    while True:
        url = (f"{shop['site']}/pl/api/Product/GetVirtualListing?page={page}&filterAttributes={fa}"
               f"&listingUrl={dept['path']}&pageSize=200&language=pl_PL&clientUuid=00000000-0000-0000-0000-000000000000")
        text, _ = f.browser_get(url, warmup=shop["site"] + "/pl/pl/", headers=headers)
        d = json.loads(text).get("data") or {}
        for p in d.get("products") or []:
            pr = p.get("price") or {}
            yield {
                "url": f"{shop['site']}/pl/pl/p/{p.get('slug')}",
                "name": f"{p.get('trademark') or ''} {p.get('name') or ''}".strip(),
                "image": (p.get("photos") or [None])[0],
                "current": num(pr.get("priceWithDiscount")) or num(pr.get("price")),
                "regular": num(pr.get("price")),
                "lowest30": num((pr.get("omnibusPrice") or {}).get("price")), "sizes": [],
                "best": is_best([x.get("label") for x in p.get("stickers") or []]),
                "cat": " ".join(str(v) for v in (p.get("categoryTree") or {}).values() if v),
            }
        pages = int(d.get("pagesQuantity") or 1)
        log(f"    strona {page}/{pages} ({d.get('productsQuantity')} produktów)")
        if page >= pages or page >= 50:
            return
        page += 1


# ---------------------------------------------------------------- eobuwie (karty produktów w HTML)

def scan_eobuwie(f, shop, dept):
    import html as htmllib
    seen = set()
    for page in range(1, 81):
        url = shop["site"] + dept["path"] + (f"?p={page}" if page > 1 else "")
        page_html, _ = f.browser_get(url, warmup=shop["site"] + "/")
        new = 0
        for t in page_html.split("data-product-card=")[1:]:
            m = re.search(r'href="(/p/[^"]+)"', t)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new += 1
            g = lambda rx: (re.search(rx, t, re.S) or [None, None])[1]
            current = num(g(r"Aktualna cena\s*</span>.*?<span>\s*([\d\s.,]+)\s*</span>"))
            title, desc = g(r'class="title"[^>]*>([^<]+)<') or "", g(r'class="description"[^>]*>([^<]+)<') or ""
            yield {
                "url": shop["site"] + m.group(1),
                "name": htmllib.unescape(f"{title.strip()} {desc.strip()}").strip(),
                "image": htmllib.unescape(g(r'<img[^>]+src="([^"]+)"') or "") or None,
                "current": current,
                "regular": num(g(r"Cena regularna\s*<span[^>]*>\s*<span[^>]*>([^<]+)<")) or current,
                "lowest30": num(g(r"Najniższa cena\s*<span[^>]*>\s*<span[^>]*>([^<]+)<")), "sizes": [],
                "best": is_best(" ".join(re.findall(r'class="badge-content"[^>]*>([^<]+)<', t))),
            }
        log(f"    strona {page}: {len(seen)} produktów")
        if not new:
            return


# ---------------------------------------------------------------- Sizeer (karty produktów w HTML)

def scan_sizeer(f, shop, dept):
    import html as htmllib
    seen = set()
    for page in range(1, 81):
        url = shop["site"] + dept["path"] + (f"?page={page}" if page > 1 else "")
        page_html, _ = f.browser_get(url, warmup=shop["site"] + "/")
        # zdjęcia są w zakodowanych danych strony: link produktu → pierwszy plik galerii
        gallery = dict(re.findall(r'"link":"(/[^"]+)","gallery":\["([^"]+)"', urllib.parse.unquote(page_html)))
        new = 0
        for t in page_html.split('data-offer-id="')[1:]:
            m = re.search(r'href="(/[^"#?]+)" class="title', t)
            if not m or m.group(1) in seen:
                continue
            href = m.group(1)
            seen.add(href)
            new += 1
            offer = t.split('"', 1)[0]
            attr = lambda k: htmllib.unescape((re.search(rf'data-{k}="([^"]*)"', t) or [None, ""])[1])
            omni = t.find('class="omnibus-price"')
            olds = re.findall(r'class="old-price is-line-through"[^>]*>\s*([\d\s.,]+)zł', t[:omni] if omni > 0 else t)
            low = re.search(r'class="omnibus-price".*?is-line-through"[^>]*>\s*([\d\s.,]+)zł', t, re.S)
            current = num(attr("price"))
            yield {
                "url": shop["site"] + href,
                "name": attr("name") if attr("brand").upper() in attr("name").upper() else f"{attr('brand')} {attr('name')}".strip(),
                "image": (f"{shop['site']}/api/images/offer-{offer}/seo/gallery_455_455/thumbnails/{gallery[href]}"
                          if href in gallery else None),
                "current": current, "regular": num(olds[0]) if olds else current,
                "lowest30": num(low.group(1)) if low else None, "sizes": [],
                "best": is_best(" ".join(re.findall(r'class="emblem-content"[^>]*><p>([^<]+)<', t))),
                "cat": attr("category"),
            }
        log(f"    strona {page}: {len(seen)} produktów")
        if not new:
            return


# ---------------------------------------------------------------- 50style (karty produktów w HTML)

def scan_50style(f, shop, dept):
    import html as htmllib
    seen = set()
    for page in range(1, 81):
        url = shop["site"] + dept["path"] + (f"?page={page}" if page > 1 else "")
        page_html, _ = f.browser_get(url, warmup=shop["site"] + "/")
        new = 0
        for t in page_html.split('js-offerItem"')[1:]:
            m = re.search(r'class="item-name" href="([^"]+)"', t)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new += 1
            g = lambda rx: (re.search(rx, t, re.S) or [None, None])[1]
            current = num(g(r'item-main-price"[^>]*>\s*([\d\s.,]+)zł'))
            yield {
                "url": shop["site"] + m.group(1),
                "name": htmllib.unescape((g(r'data-name="([^"]*)"') or "").strip()),
                "image": g(r'data-src="([^"]+)"'),
                "current": current,
                "regular": num(g(r'item-price-old"[^>]*>\s*([\d\s.,]+)zł')) or current,
                "lowest30": num(g(r'item-price-omnibus">\s*<span>([^<]+)<')), "sizes": [],
                "best": is_best(" ".join(re.findall(r"<p><b>([^<]+)</b></p>", t))),
                "cat": " ".join(re.findall(r'data-(?:ga-)?category="([^"]*)"', t)),
            }
        log(f"    strona {page}: {len(seen)} produktów")
        if not new:      # 50style po ostatniej stronie zwraca ją ponownie
            return


# ---------------------------------------------------------------- Deichmann (karty produktów w HTML)

def scan_deichmann(f, shop, dept):
    import html as htmllib
    seen, code, pct, note = set(), dept.get("code"), None, ""
    for page in range(1, 81):
        url = shop["site"] + dept["path"] + (f"?page={page}" if page > 1 else "")
        page_html, _ = f.browser_get(url, warmup=shop["site"] + "/pl-pl/")
        if code and pct is None:
            # rabat z kodem odczytujemy z banera działu, np. „OUTLET -30% na ostatnie pary przy zakupach powyżej 120 zł Kod: OUTLET”
            text = htmllib.unescape(re.sub(r"<[^>]+>", " ", page_html))
            m = re.search(rf"{code}\s*-\s*(\d+)\s*%\s*([^%]{{0,90}}?)\s*Kod:\s*{code}", text)
            pct, note = (int(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip()) if m else (0, "")
            log(f"    kod {code}: {'-' + str(pct) + '% ' + note if pct else 'nie znaleziono wysokości rabatu – pomijam dział'}")
            if not pct:
                return
        new = 0
        for t in page_html.split("<deich-lib-product-tile")[1:]:
            m = re.search(r'href="(/pl-pl/p/[^"]+)"', t)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            new += 1
            g = lambda rx: (re.search(rx, t, re.S) or [None, None])[1]
            selling = num(g(r'data-id="selling-price"[^>]*>\s*([\d\s.,]+)'))
            regular = num(g(r"Cena regularna:</span>\s*<span[^>]*>([^<]+)<")) or selling
            brand, kind = g(r'class="brandname"[^>]*>([^<]+)<') or "", g(r'class="taglist"[^>]*>([^<]+)<') or ""
            p = {
                "url": shop["site"] + m.group(1),
                "name": htmllib.unescape(f"{brand.strip()} {kind.strip()}").strip(),
                "image": g(r'<img[^>]+src="(https://asset\.deichmann\.com[^"]+)"'),
                "current": selling, "regular": regular,
                "lowest30": num(g(r"Najniższa cena:</span>\s*<span[^>]*>([^<]+)<")), "sizes": [],
                "best": is_best(" ".join(re.findall(r'data-testid="([^"]+)"', t))),
            }
            if code and selling:
                p.update(current=round(selling * (1 - pct / 100), 2), code=code,
                         code_note=f"−{pct}% z kodem {code}" + (f" ({note})" if note else ""))
            yield p
        log(f"    strona {page}: {len(seen)} produktów")
        if not new:
            return


# ---------------------------------------------------------------- Zalando (dane produktów zapisane w stronie)

def scan_zalando(f, shop, dept):
    seen = set()
    # shortcut: dział „Promocja” ma dziesiątki tysięcy produktów – bierzemy tylko N stron posortowanych
    # od największego rabatu; zwiększ "max_pages" w config.json, jeśli chcesz więcej
    for page in range(1, dept.get("max_pages", 25) + 1):
        url = f"{shop['site']}{dept['path']}?order=sale" + (f"&p={page}" if page > 1 else "")
        page_html, _ = f.browser_get(url, warmup=shop["site"] + "/")
        m = re.search(r"window\.__hydrationDataConsume\((.*?)\);?</script>", page_html, re.S)
        if not m:
            raise RuntimeError("brak danych produktów na stronie")
        prods = []

        def walk(o):
            if isinstance(o, dict):
                if str(o.get("id", "")).startswith("ern:product::") and o.get("name") and o.get("displayPrice"):
                    prods.append(o)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(json.loads(m.group(1)))
        new = 0
        for p in prods:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            new += 1
            dp = p["displayPrice"]
            texts = re.findall(r'"text":\s*"([^"]+)"', json.dumps(p.get("displayPriceModule"), ensure_ascii=False))
            low = next((texts[i + 1] for i, t in enumerate(texts[:-1]) if "Najniższa cena" in t), None)
            yield {
                "url": p.get("uri"),
                "name": f"{(p.get('brand') or {}).get('name', '')} {p.get('name', '')}".strip(),
                "image": (p.get("mediumDefaultMedia") or {}).get("uri"),
                "current": money((dp.get("promotional") or {}).get("amount") or (dp.get("original") or {}).get("amount")),
                "regular": money((dp.get("original") or {}).get("amount")),
                "lowest30": num(low), "sizes": [x.get("size") for x in p.get("simples") or [] if x.get("size")],
                "best": any(fl.get("kind") == "BESTSELLER" for fl in p.get("productFlags") or []),
                "cat": "shoes" if re.search(r"SHOE|SNEAKER|BOOT|SANDAL|SLIPPER|PUMP|BALLERINA|LOAFER", p.get("silhouette") or "") else "",
            }
        log(f"    strona {page}: {len(seen)} produktów")
        if not new:
            return


SCANNERS = {"lpp": scan_lpp, "answear": scan_answear, "hm": scan_hm,
            "esotiq": scan_esotiq, "calz": scan_calz, "sfra": scan_sfra,
            "ccc": scan_ccc, "eobuwie": scan_eobuwie, "sizeer": scan_sizeer, "50style": scan_50style,
            "deichmann": scan_deichmann, "zalando": scan_zalando}
# sklepy, w których robot sam znajduje działy promocji
SHOP_SCANNERS = {"zara": scan_zara, "itx": scan_itx}


def scan_shop(f, shop):
    """Skanuje jeden sklep; zwraca (produkty, liczba)."""
    products, skipped = {}, 0

    def add(p, label, best=False):
        nonlocal skipped
        if not p["url"] or not p["name"] or not p["current"] or not p["regular"]:
            return
        if p.pop("available", True) is False:      # wyprzedany – nie da się kupić
            skipped += 1
            return
        p["best"] = bool(p.get("best") or best)
        cat = p.pop("cat", "")
        if KIDS_RE.search(f"{p['name']} {cat}"):   # produkty dziecięce z działów mieszanych (działy dziecięce są wyłączone)
            return
        p["shoes"] = bool(SHOE_RE.search(f"{p['name']} {cat} {label}"))
        if p["url"] in products:
            products[p["url"]]["best"] = products[p["url"]].get("best") or p["best"]
            return
        p.update(shop=shop["name"], category=label,
                 current=round(p["current"], 2), regular=round(p["regular"], 2),
                 sizes=list(dict.fromkeys(str(x) for x in p.get("sizes") or []))[:14])
        products[p["url"]] = p

    if shop["type"] in SHOP_SCANNERS:
        try:
            for label, p in SHOP_SCANNERS[shop["type"]](f, shop):
                add(p, label)
        except Exception as e:
            log(f"  POMIJAM ({e})")
    for dept in shop.get("departments", []):
        log(f"  {dept['label']}")
        try:
            for p in SCANNERS[shop["type"]](f, shop, dept):
                add(p, dept["label"], best=dept.get("best", False))
        except Exception as e:
            log(f"    POMIJAM ({e})")
    log(f"  {shop['name']}: {len(products)} przecenionych produktów (pominięto niedostępnych: {skipped})")
    return products


def scan(cfg):
    from concurrent.futures import ThreadPoolExecutor
    f = Fetch(cfg["delay_seconds"])
    shops = [s for s in cfg["shops"] if s.get("enabled", True)]
    # różne firmy skanowane równolegle – przy 20 sklepach skan po kolei trwał ok. 17 minut
    # sklepy jednej firmy ("group" w config.json) mają wspólne serwery – skanujemy je po kolei w jednym wątku,
    # więc żadna firma nie dostaje więcej zapytań naraz niż przy skanowaniu po kolei
    groups = {}
    for shop in shops:
        groups.setdefault(shop.get("group", shop["name"]), []).append(shop)

    def run(group):
        import threading
        out = {}
        for shop in group:
            threading.current_thread().name = shop["name"]
            out[shop["name"]] = scan_shop(f, shop)
        return out
    with ThreadPoolExecutor(max_workers=cfg.get("parallel_shops", 6)) as pool:
        results = {}
        for r in pool.map(run, groups.values()):
            results.update(r)
    products, per_shop = {}, {}
    for shop in shops:
        res = results.get(shop["name"], {})
        per_shop[shop["name"]] = len(res)
        for url, p in res.items():
            products.setdefault(url, p)
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
            "best": bool(p.get("best")),
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
            # tylko jeden skan wstecz – inaczej zakończona wyprzedaż albo martwy sklep wisiałyby w nieskończoność
            old = [dict(d, stale=True) for d in prev if d.get("shop") == shop and not d.get("stale")]
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
    SKIP_DEPARTMENTS.update(cfg.get("skip_departments", []))

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
