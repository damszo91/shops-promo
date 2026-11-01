#!/usr/bin/env python3
"""
Shops Promo – skaner okazji (na razie Sinsay).

Robot pobiera z Sinsay listę produktów z filtrem „Tylko produkty przecenione”
(to ten sam filtr, którego używa strona sklepu) dla każdego działu:
Kobieta, Mężczyzna, Dziecko, Dom, Buty, Uroda.

Następnie:
- zapisuje historię cen w data/history.json (wykrywanie naciąganych rabatów),
- generuje stronę docs/index.html z okazjami.

Nie wymaga żadnych dodatkowych bibliotek – wystarczy Python 3.9+.

Uruchomienie:  python scraper.py
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Origin": "https://www.sinsay.com",
    "Referer": "https://www.sinsay.com/pl/pl/",
}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def http_get(url, as_json=True, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            log(f"  błąd HTTP {e.code}, ponawiam…")
        except Exception as e:
            log(f"  błąd połączenia ({e}), ponawiam…")
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"Nie udało się pobrać: {url}")


def resolve_category_id(cfg, dept):
    """Gdyby Sinsay zmienił numer kategorii – odczytaj aktualny ze strony działu."""
    html = http_get(cfg["site_url"] + dept["path"], as_json=False)
    m = re.search(r"categoryId:\s*(\d+)", html)
    if not m:
        raise RuntimeError(f"Nie znaleziono numeru kategorii dla {dept['label']}")
    return int(m.group(1))


def to_num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def parse_product(p, label):
    current = to_num(p.get("final_price")) or to_num(p.get("minQtyFinalPrice"))
    regular = to_num(p.get("price")) or to_num(p.get("minQtyRegularPrice"))
    if not current or not regular:
        return None
    img = None
    try:
        img = p["images"]["850"]["front"]
    except Exception:
        try:
            img = p["firstPhoto"]["sizes"]["medium"]
        except Exception:
            img = (p.get("img") or [None])[0]
    sizes = []
    for s in p.get("sizes") or []:
        if isinstance(s, dict) and s.get("stock") and s.get("sizeName"):
            sizes.append(str(s["sizeName"]))
    return {
        "url": (p.get("url") or "").split("?")[0],
        "sku": p.get("sku"),
        "name": (p.get("namePl") or p.get("name") or "").strip(),
        "image": img,
        "current": round(current, 2),
        "regular": round(regular, 2),
        "lowest30": to_num(p.get("historyPrice")),
        "category": label,
        "sizes": sizes[:12],
    }


def scan(cfg):
    products = {}
    for dept in cfg["departments"]:
        try:
            scan_department(cfg, dept, products)
        except Exception as e:
            log(f"  POMIJAM dział {dept['label']}: {e}")
        time.sleep(cfg["delay_seconds"])
    return list(products.values())


def scan_department(cfg, dept, products):
        label, cat_id = dept["label"], dept.get("id")
        log(f"Dział: {label}")
        offset, total, page_size = 0, None, cfg["page_size"]
        while True:
            url = (f"{cfg['api_url']}{cfg['store_id']}/category/{cat_id}/products"
                   f"?offset={offset}&pageSize={page_size}&filters[hasDiscount]=1")
            try:
                data = http_get(url)
            except urllib.error.HTTPError as e:
                if offset == 0 and not dept.get("_resolved"):
                    log(f"  kategoria nie odpowiada (HTTP {e.code}) – szukam aktualnego numeru")
                    dept["id"] = cat_id = resolve_category_id(cfg, dept)
                    dept["_resolved"] = True
                    continue
                raise
            items = data.get("products") or []
            total = int(data.get("productsTotalAmount") or 0)
            for raw in items:
                p = parse_product(raw, label)
                if p and p["url"] and p["url"] not in products:
                    products[p["url"]] = p
            offset += len(items)
            log(f"  pobrano {offset}/{total}")
            if not items or offset >= total or offset >= cfg["max_per_department"]:
                break
            time.sleep(cfg["delay_seconds"])


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
        if not p["name"]:
            continue
        h = hist.setdefault(p["url"], {"p": []})
        prev = [x for x in h["p"] if x[0] >= cutoff]
        min_before = min((x[1] for x in prev), default=None)
        last = prev[-1] if prev else None
        seen_last_scan = bool(last and h.get("ls") == hist.get("_last_scan"))

        discount = round((1 - p["current"] / p["regular"]) * 100) if p["regular"] > p["current"] else 0
        if discount < cfg["min_discount_percent"]:
            continue
        if not (seen_last_scan and h.get("since")):
            h["since"] = now_s
        deals.append({
            **p,
            "discount": discount,
            "sale_since": h["since"],
            "price_drop": bool(last and p["current"] < last[1]),
            "was_cheaper": bool(min_before is not None and min_before < p["current"] - 0.01),
            "min_seen": min(min_before, p["current"]) if min_before is not None else p["current"],
            "real_discount30": (round((1 - p["current"] / p["lowest30"]) * 100)
                                if p.get("lowest30") else None),
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


def build_page(deals, cfg):
    DOCS.mkdir(exist_ok=True)
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shop": "Sinsay",
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

    products = scan(cfg)
    log(f"Łącznie przecenionych produktów: {len(products)}")
    if not products:
        log("BŁĄD: nie pobrano żadnych produktów – strona NIE została nadpisana.")
        sys.exit(1)
    deals = update_history(products, cfg)
    build_page(deals, cfg)
    log(f"Gotowe: {len(deals)} okazji (rabat ≥ {cfg['min_discount_percent']}%) zapisano w docs/index.html")


if __name__ == "__main__":
    main()
