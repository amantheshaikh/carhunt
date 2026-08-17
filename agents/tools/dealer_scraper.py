"""
Dealer website inventory scraping.

Scraping strategies (tried in order for Playwright-based sites):
  1. DealerOn data-vehicle JSON
  2. DealerOn per-attribute data-* (data-vehicle-information)
  3. Dealer.com / AutoNation / Cox li.vehicle-card[data-uuid]
  4. Algolia InstantSearch li.ais-Hits-item (DOM text parsing)
  5. DealerInspire .result-wrap[data-vin] (detail-page mileage fetch)
  6. JSON-LD Vehicle schema

API-based strategies (configured via scraper_config JSONB column):
  overfuel      — ALM dealers
  carscommerce  — CDK-hosted sites (e.g. Potamkin Hyundai)
  ridemotive    — RideMotive / Algolia (e.g. Jim Ellis Kia)
  secureoffersites — Vue.js SSR (e.g. Hyundai of Cumming)
  dealeron_cosmos  — DealerOn spaCosmos SPA (e.g. Hyundai of Kennesaw, Ed Voyles Kia)
"""
import asyncio
import concurrent.futures
import json
import os
import random
import re
import requests
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from tools.db import fetch_all, execute
from tools.scrapers import _stealth_browser, _UA_POOL

_UA = random.choice(_UA_POOL)   # pick once per process; rotates each orchestrator run


# ---------------------------------------------------------------------------
# Overfuel API (ALM dealers)
# ---------------------------------------------------------------------------

_OVERFUEL_HEADERS = {
    "User-Agent": _UA,
    "Referer": "https://www.almcars.com/",
    "Origin": "https://www.almcars.com",
}

_OVERFUEL_BASE_URL = "https://www.almcars.com"


def _is_overfuel_url(url: str) -> bool:
    return "api.overfuel.com" in url


def _build_dealer_com_url(inventory_url: str, makes: list[str] | None, min_year: int, max_year: int | None) -> str:
    if "index.htm" not in inventory_url:
        return inventory_url
    # Require Dealer.com (CDK) inventory path segments to avoid injecting params into
    # unrelated CMSes (e.g. DealerInspire) that also happen to have index.htm in their URLs
    _DC_PATHS = ("/used-inventory/", "/new-inventory/", "/certified-inventory/", "/pre-owned/")
    if not any(p in inventory_url for p in _DC_PATHS):
        return inventory_url

    parsed = urlparse(inventory_url)
    params = parse_qs(parsed.query)
    
    # Clean up parameters we are going to supply ourselves
    for key in ["make", "model", "year", "start", "pageNum"]:
        params.pop(key, None)
        
    # Build our parameters — filter by make only (model names in cars.json often don't match
    # Dealer.com model names, e.g. "NX 350h" vs "NX", causing false-zero results)
    if makes:
        params["make"] = list(makes)

    # Year filter — Dealer.com expects individual year values, not a range string
    if min_year:
        max_yr = max_year or datetime.now().year + 2
        params["year"] = [str(y) for y in range(min_year, max_yr + 1)]
        
    # Reconstruct URL
    query_str = urlencode(params, doseq=True)
    sep = "?" if query_str else ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{sep}{query_str}"


def _build_dealeron_url(inventory_url: str, makes: list[str] | None, min_year: int, max_year: int | None) -> str:
    is_dealeron = any(p in inventory_url.lower() for p in ["searchused.aspx", "searchnew.aspx", "search.aspx", "inventory.aspx"])
    if not is_dealeron:
        return inventory_url
    if "amazingluxurycars.com" in inventory_url:
        return inventory_url

    parsed = urlparse(inventory_url)
    params = parse_qs(parsed.query)
    
    # Clean up parameters we are going to supply ourselves
    for key in ["Make", "MakeModelTrim", "ModelAndTrim", "yearrange", "year", "pageNum", "start", "q"]:
        params.pop(key, None)
        
    # Build ModelAndTrim
    from tools.base_trims import _VEHICLES
    models = []
    for v in _VEHICLES:
        model = v["model"]
        models.append(model)
        if " Hybrid" in model:
            models.append(model.replace(" Hybrid", ""))
        if " Recharge" in model:
            models.append(model.replace(" Recharge", ""))
        if " 350h" in model:
            models.append(model.replace(" 350h", ""))
    models = sorted(list(set(models)))
    params["ModelAndTrim"] = ["!".join(models)]
    
    # Build yearrange
    if min_year:
        max_yr = max_year or datetime.now().year + 2
        params["yearrange"] = [f"{min_year}-{max_yr}"]
        
    # Reconstruct URL
    query_str = urlencode(params, doseq=True)
    query_str = query_str.replace("%21", "!")
    sep = "?" if query_str else ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{sep}{query_str}"



# ---------------------------------------------------------------------------
# Cobalt/CDK Group dealer sites (Lexus, Toyota, many others)
# These sites load inventory via POST /api/widget/ws-inv-data/getInventory.
# We intercept API responses and click through pagination rather than scraping DOM.
# ---------------------------------------------------------------------------

async def _scrape_cobalt_async(inventory_url: str, brand: str,
                               makes: list[str] | None = None,
                               min_year: int = 0, max_year: int | None = None) -> list[dict]:
    inventory: list[dict] = []
    seen_ids: set[str] = set()
    base_url = "/".join(inventory_url.split("/")[:3])

    def _extract_cobalt_price(pricing: dict) -> int | None:
        """Extract numeric price from Cobalt pricing dict."""
        if not isinstance(pricing, dict):
            return None
        # Try final/internet price from dprice array first
        for dp in (pricing.get("dprice") or []):
            if isinstance(dp, dict) and dp.get("isFinalPrice"):
                raw = dp.get("value", "")
                if not isinstance(raw, str):
                    continue  # skip nested dicts — str(dict) would produce garbage digits
                return _int(raw) or None
        # Fall back to retailPrice string
        raw = pricing.get("retailPrice") or pricing.get("salePrice") or ""
        if not isinstance(raw, str):
            return None
        return _int(raw) or None

    def _extract_cobalt_mileage(item: dict) -> int | None:
        """Extract mileage from Cobalt item — may be in attributes list or trackingAttributes."""
        tracking = item.get("trackingAttributes") or {}
        if isinstance(tracking, dict):
            m = tracking.get("mileage") or tracking.get("odometer")
            if m: return _int(re.sub(r"[^0-9]", "", str(m)))
        for attr in (item.get("attributes") or []):
            if not isinstance(attr, dict): continue
            name = (attr.get("name") or attr.get("label") or "").lower()
            if "mileage" in name or "odometer" in name or "miles" in name:
                return _int(re.sub(r"[^0-9]", "", str(attr.get("value", "")))) or None
        return None

    async def _on_response(resp):
        if "getInventory" in resp.url and resp.status == 200:
            try:
                data = await resp.json()
                for item in data.get("inventory", []):
                    if not isinstance(item, dict):
                        continue
                    vin   = item.get("vin") or None
                    uuid  = item.get("uuid") or item.get("stockNumber") or ""
                    ext_key = vin or uuid
                    if not ext_key or ext_key in seen_ids:
                        continue
                    seen_ids.add(ext_key)
                    make  = item.get("make", "")
                    model = item.get("model", "")
                    trim  = item.get("trim") or None
                    year  = item.get("year")
                    link  = item.get("link") or ""
                    detail_url = (base_url + link) if link and not link.startswith("http") else link
                    pricing = item.get("pricing") or {}
                    price = _extract_cobalt_price(pricing)
                    mileage = _extract_cobalt_mileage(item)
                    images = item.get("images") or []
                    photo = images[0].get("url") if images and isinstance(images[0], dict) else None
                    if not price:
                        continue
                    url = detail_url or inventory_url
                    cpo = "/certified" in url.lower()
                    inventory.append(_make_listing(
                        brand, ext_key,
                        vin=vin, make=make, model=model, trim=trim,
                        year=int(year) if year else None, body_type=None,
                        mileage=mileage, price=price,
                        listing_url=url,
                        photos_url=[photo] if photo else [],
                        dealer_name=brand,
                        insights={"cpo": cpo} if cpo else None,
                        raw={"vin": vin, "year": year, "make": make, "model": model,
                             "trim": trim, "price": price, "mileage": mileage},
                    ))
            except Exception as e:
                print(f"    [cobalt] response parse error ({resp.url[-50:]}): {e}", flush=True)

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        # Register BEFORE goto so we don't miss responses on fast networks
        page.on("response", _on_response)
        # Navigate to bare URL — URL-level make/year params break the Dealer.com SPA's
        # getInventory POST body, causing it to return 0 results. Post-filtering in _add_used
        # covers the same ground without touching the SPA's API call.
        try:
            await page.goto(inventory_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(6_000)
            # Click through all pagination pages
            for _ in range(50):
                prev = len(inventory)
                await page.evaluate("""() => {
                    document.querySelectorAll('[id*="poke"],[class*="chat"],[class*="overlay"]')
                        .forEach(el => el.remove());
                }""")
                clicked = await page.evaluate("""() => {
                    const btn = document.querySelector(
                        '.pagination [aria-label*="Next" i],.pagination [title*="Next" i],' +
                        '.pagination button:last-child,[data-next-page],[aria-label="Go to next page"]'
                    );
                    if (btn && !btn.disabled) { btn.click(); return true; }
                    return false;
                }""")
                if not clicked:
                    break
                await page.wait_for_timeout(2_500)
                if len(inventory) == prev:
                    break
        finally:
            await browser.close()
    return inventory


# ---------------------------------------------------------------------------
# Dealer.com (DDC) SSR inventory pages
# Some DDC sites (e.g. Rick Case Hyundai Atlanta) no longer fire getInventory
# XHR — inventory is pre-rendered as SSR HTML using li.vehicle-card elements.
# We paginate via ?start=N and parse the DOM directly.
# ---------------------------------------------------------------------------

async def _scrape_cobalt_dom_async(inventory_url: str, brand: str,
                                    makes: list[str] | None = None,
                                    min_year: int = 0, max_year: int | None = None) -> list[dict]:
    inventory: list[dict] = []
    seen_vins: set[str] = set()
    is_cpo_page = "certified" in inventory_url.lower()

    # Add make/year server-side filters (DDC SSR supports standard query params)
    filtered_url = _build_dealer_com_url(inventory_url, makes, min_year, max_year)

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()

        try:
            start = 0
            page_size = 24  # DDC SSR default

            while True:
                parsed = urlparse(filtered_url)
                params = parse_qs(parsed.query, keep_blank_values=True)
                params["start"] = [str(start)]
                page_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(params, doseq=True)}"

                await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2_500)

                cards_data = await page.evaluate("""() => {
                    const cards = [...document.querySelectorAll('li.vehicle-card')];
                    return cards.map(card => {
                        // Title: first line matching "YYYY Make ..." pattern
                        const bodyText = card.innerText || '';
                        const titleLine = bodyText.split('\\n')
                            .map(l => l.trim())
                            .find(l => /^\\d{4}\\s+[A-Z]/.test(l)) || '';

                        // VIN from roadster button or any data-vin attr
                        const vinEl = card.querySelector('[data-vin]');
                        const vin = vinEl?.getAttribute('data-vin') || null;

                        // Mileage
                        const mileageM = bodyText.match(/([\\d,]+)\\s*miles?/i);
                        const mileage = mileageM ? mileageM[1].replace(/,/g, '') : null;

                        // Sale Price: first .price-value span (advertised price before fees)
                        const priceEl = card.querySelector('.price-value');
                        const priceText = priceEl?.innerText?.trim() || '';
                        const priceM = priceText.match(/[\\d,]+/);
                        const price = priceM ? priceM[0].replace(/,/g, '') : null;

                        // Detail URL
                        const linkEl = card.querySelector('a[href*="/used/"],a[href*="/certified/"],a[href*="/pre-owned/"]')
                            || card.querySelector('a[href$=".htm"]');
                        const href = linkEl?.href || '';

                        const isCpo = href.includes('/certified') ||
                            bodyText.toLowerCase().includes('certified pre-owned');

                        return { titleLine, vin, mileage, price, href, isCpo };
                    });
                }""")

                if not cards_data:
                    break

                skipped = 0
                for card in cards_data:
                    vin = card.get("vin")
                    if vin and vin in seen_vins:
                        skipped += 1
                        continue

                    title = card.get("titleLine", "").strip()
                    if not title:
                        skipped += 1
                        continue

                    year_s, make, model, trim = _parse_title(title)
                    if not year_s or not make or not model:
                        skipped += 1
                        continue

                    mileage = int(card["mileage"]) if card.get("mileage") else None
                    price = int(card["price"]) if card.get("price") else None
                    href = card.get("href") or inventory_url
                    is_cpo = is_cpo_page or card.get("isCpo", False)

                    if not price:
                        skipped += 1
                        continue

                    ext_id = vin or re.sub(r'[^a-z0-9]', '', href.lower())[-20:]
                    inventory.append(_make_listing(
                        brand, ext_id,
                        vin=vin, make=make, model=model, trim=trim or None,
                        year=int(year_s) if year_s else None, body_type=None,
                        mileage=mileage, price=price,
                        listing_url=href, photos_url=[],
                        dealer_name=brand,
                        insights={"cpo": is_cpo} if is_cpo else None,
                        raw={"title": title, "vin": vin, "price": price, "mileage": mileage},
                    ))
                    if vin:
                        seen_vins.add(vin)

                if skipped:
                    print(f"    [cobalt_dom] page start={start}: {len(cards_data) - skipped} parsed, {skipped} skipped (no title/price/vin-dup)", flush=True)

                if len(cards_data) < page_size:
                    break

                start += page_size
                if start > 1200:  # 50-page safety limit
                    break

        finally:
            await browser.close()

    return inventory


# ---------------------------------------------------------------------------
# DealerOn spaCosmos SPA
# The SPA calls /api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/{dealer_id}/{page_id}.
# We intercept the first request to discover those IDs, then paginate via httpx.
# ---------------------------------------------------------------------------

async def _scrape_dealeron_cosmos_async(inventory_url: str, brand: str,
                                         makes: list[str] | None = None,
                                         min_year: int = 0,
                                         max_year: int | None = None,
                                         cosmos_path: str | None = None) -> list[dict]:
    import httpx

    base_url = "/".join(inventory_url.split("/")[:3])

    # If dealer_id + page_id are pre-configured, build cosmos_path directly
    # and skip the browser entirely — faster and avoids SPA-detection issues.
    if not cosmos_path:
        async with Stealth().use_async(async_playwright()) as p:
            browser, ctx = await _stealth_browser(p)
            page = await ctx.new_page()

            async def _on_response(resp):
                nonlocal cosmos_path
                if "/api/vhcliaa/vehicle-pages/cosmos/srp/" in resp.url:
                    parsed = urlparse(resp.url)
                    path = parsed.path.replace("/srp/filters/", "/srp/vehicles/")
                    cosmos_path = f"{parsed.scheme}://{parsed.netloc}{path}"

            page.on("response", _on_response)
            try:
                await page.goto(inventory_url, wait_until="domcontentloaded", timeout=30_000)
                for _ in range(40):
                    if cosmos_path:
                        break
                    await page.wait_for_timeout(500)
            finally:
                await browser.close()

    if not cosmos_path:
        return []

    # Strip any embedded query params — we supply our own via urlencode(params)
    cosmos_path = cosmos_path.split("?")[0]

    from tools.base_trims import _VEHICLES as _VEH
    max_yr = max_year or datetime.now().year + 2
    host = urlparse(inventory_url).netloc

    # Build make/model filter (same ! separator as DealerOn legacy)
    models: list[str] = []
    for v in _VEH:
        if not makes or v["make"].lower() in [m.lower() for m in makes]:
            m = v["model"]
            models.append(m)
            for suffix in (" Hybrid", " Recharge", " 350h", " PHEV"):
                if m.endswith(suffix):
                    models.append(m[: -len(suffix)])
    models = sorted(set(models))

    # Build params as list of tuples — Make must repeat per make (! separator not supported)
    base_params: list[tuple] = [
        ("host", host),
        ("baseFilter", "dHlwZT0ndSc="),  # base64("type='u'") — used vehicles only
        ("displayCardsShown", 24),
    ]
    if min_year:
        base_params.append(("yearrange", f"{min_year}-{max_yr}"))
    for m in (makes or []):
        base_params.append(("Make", m))
    if models:
        base_params.append(("ModelAndTrim", "!".join(models)))

    all_cards: list[dict] = []
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
        page_num = 1
        while True:
            params = base_params + [("pageNumber", page_num)]
            resp = await client.get(f"{cosmos_path}?{urlencode(params)}")
            data = resp.json()
            cards = [
                item["VehicleCard"]
                for item in data.get("DisplayCards", [])
                if not item.get("IsAdCard") and item.get("VehicleCard")
            ]
            if not cards:
                break
            all_cards.extend(cards)
            paging = data.get("Paging", {}).get("PaginationDataModel", {})
            if paging and page_num >= paging.get("TotalPages", 1):
                break
            page_num += 1

    return [_normalize_cosmos_vehicle(c, brand, base_url) for c in all_cards]


def _normalize_cosmos_vehicle(card: dict, brand: str, base_url: str) -> dict:
    mi_m = re.search(r"[\d,]+", card.get("Mileage") or "")
    mileage = int(mi_m.group().replace(",", "")) if mi_m else None

    price_raw = card.get("TaggingPrice") or card.get("VehicleMsrp") or 0
    try:
        price = int(float(str(price_raw).replace(",", ""))) or None
    except (ValueError, TypeError):
        price = None

    href = card.get("VehicleDetailUrl") or ""
    if href and not href.startswith("http"):
        href = base_url.rstrip("/") + "/" + href.lstrip("/")

    cond_raw = (card.get("VehicleCondition") or "").lower()
    if card.get("VehicleCpo") or "certified" in cond_raw:
        condition = "cpo"
    elif "new" in cond_raw:
        condition = "new"
    else:
        condition = "used"

    vin = card.get("VehicleVin") or None
    stock = card.get("VehicleStockNumber") or None
    return _make_listing(
        brand, vin or stock or href[-20:],
        vin=vin, make=(card.get("VehicleMake") or "").title(),
        model=card.get("VehicleModel") or "",
        trim=card.get("VehicleTrim") or None,
        year=card.get("VehicleYear"),
        body_type=(card.get("VehicleBodyType") or "").lower() or None,
        mileage=mileage, price=price,
        listing_url=href or base_url,
        photos_url=[card["VehiclePhotoSrc"]] if card.get("VehiclePhotoSrc") else [],
        dealer_name=base_url.replace("https://www.", "").replace("http://www.", ""),
        condition=condition, raw=card,
    )


def _scrape_overfuel(api_url: str, brand: str, website_url: str = _OVERFUEL_BASE_URL) -> list[dict]:
    """Fetch all pages from an Overfuel API search URL (max 100/page, paginated by offset)."""
    listings: list[dict] = []
    seen_vins: set[str] = set()
    offset = 0

    if "limit=" not in api_url:
        sep = "&" if "?" in api_url else "?"
        api_url = f"{api_url}{sep}limit=100"

    while True:
        sep = "&" if "?" in api_url else "?"
        url = f"{api_url}{sep}offset={offset}"
        try:
            r = requests.get(url, headers=_OVERFUEL_HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    [overfuel] error at offset={offset}: {e}", flush=True)
            break

        results = data.get("results", [])
        if not results:
            break

        for v in results:
            vin = v.get("vin") or None
            if vin and vin in seen_vins:
                continue
            listing = _normalize_overfuel(v, brand, website_url)
            if listing:
                listings.append(listing)
                if vin:
                    seen_vins.add(vin)

        total = data.get("meta", {}).get("total", 0)
        offset += len(results)
        if offset >= total:
            break

    return listings


def _make_listing(
    brand: str,
    ext_key: str,
    *,
    vin: str | None,
    make: str,
    model: str,
    trim: str | None,
    year,
    body_type: str | None,
    mileage,
    price,
    listing_url: str,
    photos_url: list,
    dealer_name: str,
    dealer_phone: str | None = None,
    condition: str = "used",
    raw: dict | None = None,
    insights: dict | None = None,
) -> dict:
    """Assemble the standard dealer listing dict — source/title_status/accident_count/email are always the same."""
    return {
        "source":         "dealer",
        "external_id":    f"{brand}_{ext_key}",
        "vin":            vin,
        "make":           make,
        "model":          model,
        "trim":           trim,
        "year":           year,
        "body_type":      body_type,
        "mileage":        mileage,
        "price":          price,
        "condition":      condition,
        "dealer_name":    dealer_name,
        "dealer_phone":   dealer_phone,
        "dealer_email":   None,
        "listing_url":    listing_url,
        "photos_url":     photos_url,
        "title_status":   "clean",
        "accident_count": 0,
        "raw":            raw or {},
        "insights":       insights,
    }


def _normalize_overfuel(v: dict, brand: str, website_url: str = _OVERFUEL_BASE_URL) -> dict | None:
    price = v.get("price") or v.get("specialprice") or v.get("originalprice")
    if not price:
        return None
    base    = website_url.rstrip("/")
    rel_url = v.get("url", "")
    photos  = v.get("photos") or []
    if isinstance(photos, list) and photos and isinstance(photos[0], dict):
        photo_urls = [p["url"] for p in photos if p.get("url")]
    elif isinstance(photos, list):
        photo_urls = [p for p in photos if isinstance(p, str)]
    else:
        photo_urls = []
    dealer  = v.get("dealer") or {}
    return _make_listing(
        brand, v.get("vin") or v.get("stocknumber") or rel_url[-20:],
        vin=v.get("vin") or None,
        make=v.get("make", ""), model=v.get("model", ""),
        trim=v.get("trim") or v.get("series") or None,
        year=v.get("year"), body_type=(v.get("body") or "").lower() or None,
        mileage=v.get("mileage"), price=int(float(price)),
        listing_url=f"{base}{rel_url}" if rel_url else base,
        photos_url=photo_urls[:1],
        dealer_name=dealer.get("name", brand), dealer_phone=dealer.get("phone"),
        raw={k: v[k] for k in ["vin","year","make","model","trim","price","mileage","dealer_id"] if k in v},
    )


# ---------------------------------------------------------------------------
# DealerInspire + Algolia API (BMW/Cadillac dealer WordPress sites)
# DealerInspire embeds a shared Algolia app (SEWJN80HTN) with a per-dealer
# index named "{dealer_slug}_production_inventory". Credentials are extracted
# from the page JS (var algoliaConfig = {...}) and stored in scraper_config.
# ---------------------------------------------------------------------------

def _scrape_dealerinspire_algolia(app_id: str, api_key: str, index: str, brand: str, website_url: str,
                                  makes: list[str] | None = None,
                                  min_year: int = 0, max_year: int | None = None) -> list[dict]:
    """Query DealerInspire's Algolia inventory index directly — no Playwright."""
    listings: list[dict] = []
    seen_ids: set[str] = set()
    page = 0

    algolia_filters = "NOT type:New"
    if makes:
        algolia_filters += " AND (" + " OR ".join(f'make:"{m}"' for m in makes) + ")"
    # Year range is intentionally NOT filtered at Algolia level — some indexes store year as a
    # string attribute, so numeric range filters (year >= 2025) return 0 results. Year filtering
    # is handled by _add_used post-scrape instead.

    while True:
        try:
            r = requests.post(
                f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/query",
                headers={
                    "X-Algolia-Application-Id": app_id,
                    "X-Algolia-API-Key": api_key,
                    "Content-Type": "application/json",
                },
                json={"query": "", "hitsPerPage": 200, "page": page, "filters": algolia_filters},
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            print(f"    [dealerinspire_algolia] page {page} error: {e}", flush=True)
            break

        hits = data.get("hits", [])
        if not hits:
            break

        for hit in hits:
            vin     = hit.get("vin") or None
            ext_key = vin or hit.get("stock") or hit.get("api_id", "")
            ext_id  = f"{brand}_{ext_key}"
            if ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)

            # price may be an HTML string ("call for price") — try both fields
            price = None
            for price_field in ("price", "our_price"):
                raw = hit.get(price_field)
                if raw:
                    try:
                        price = int(float(raw))
                        break
                    except (ValueError, TypeError):
                        pass
            if not price:
                continue

            listings.append(_make_listing(
                brand, ext_key,
                vin=vin, make=hit.get("make", ""), model=hit.get("model", ""),
                trim=hit.get("trim") or None, year=hit.get("year"),
                body_type=(hit.get("body") or "").lower() or None,
                mileage=hit.get("miles"), price=price,
                listing_url=hit.get("link") or website_url,
                photos_url=[hit["thumbnail"]] if hit.get("thumbnail") else [],
                dealer_name=hit.get("location") or brand,
                insights={"cpo": True} if str(hit.get("certified", "0")) == "1" else None,
                raw={"vin": vin, "year": hit.get("year"), "make": hit.get("make"),
                     "model": hit.get("model"), "trim": hit.get("trim"), "price": price, "mileage": hit.get("miles")},
            ))

        if page >= data.get("nbPages", 1) - 1:
            break
        page += 1

    return listings


# ---------------------------------------------------------------------------
# Cars.com Commerce API (CDK-hosted dealer sites, e.g. Potamkin Hyundai)
# ---------------------------------------------------------------------------

def _scrape_carscommerce_api(dealer_id: str, api_key: str, brand: str, website_url: str,
                             type_slugs: list[str] | None = None,
                             allowed_states: list[str] | None = None) -> list[dict]:
    base = website_url.rstrip("/")
    headers = {
        "User-Agent": _UA,
        "x-api-key": api_key,
        "content-type": "application/json",
        "referer": f"{base}/",
        "origin": base,
    }
    body = {
        "page": 1, "perPage": 100,
        "sort": [{"field": "low_price", "order": "asc"}],
        "filters": {"status": ["publish", "modified", "pend-sale"]},
        "facetFilters": {"type_slug": type_slugs or ["Used", "Certified Used"]},
    }
    url = f"https://websites-search.api.carscommerce.inc/api/v1/listings/{dealer_id}/search"
    listings: list[dict] = []
    seen_ids: set[str] = set()  # dedup by external_id — VIN alone is unsafe when vin=None
    page_num = 1
    while True:
        body["page"] = page_num
        r = requests.post(url, headers=headers, json=body, timeout=15)
        if r.status_code != 200:
            break
        data = r.json().get("data", {})
        page_listings = data.get("listings", [])
        if not page_listings:
            break
        for v in page_listings:
            # Filter by dealer state when allowed_states is set (e.g. group-wide APIs like Rick Hendrick)
            if allowed_states:
                loc = (v.get("dealer") or {}).get("location") or ""
                state_m = re.search(r',\s*([A-Z]{2})\s+\d{5}', loc)
                if not state_m or state_m.group(1) not in allowed_states:
                    continue
            normalized = _normalize_carscommerce(v, brand, base)
            if normalized and normalized["external_id"] not in seen_ids:
                listings.append(normalized)
                seen_ids.add(normalized["external_id"])
        total = data.get("total_vehicle_count", 0)
        if len(listings) >= min(total, 2000) or len(page_listings) < 100:
            break
        page_num += 1
    return listings


def _normalize_carscommerce(v: dict, brand: str, base: str) -> dict | None:
    pricing = v.get("pricing") or {}
    price   = (pricing.get("price") or pricing.get("our_price") or
               pricing.get("internet_price") or pricing.get("selling_price") or
               pricing.get("msrp"))
    if not price:
        return None
    vin    = v.get("vin") or ""
    photos = (v.get("media") or {}).get("photos") or []
    return _make_listing(
        brand, vin or v.get("stock", ""),
        vin=vin or None,
        make=v.get("make", ""), model=v.get("model", ""),
        trim=v.get("trim") or None,
        year=v.get("year"), body_type=None,
        mileage=v.get("mileage"), price=int(float(price)),
        listing_url=v.get("vdp_url") or f"{base}/inventory/{vin}/",
        photos_url=[u for u in [p.get("url_full") or p.get("url") for p in photos if isinstance(p, dict)] if u][:1],
        dealer_name=brand,
        raw={"vin": vin, "year": v.get("year"), "make": v.get("make"),
             "model": v.get("model"), "trim": v.get("trim"), "price": price, "mileage": v.get("mileage")},
    )


# ---------------------------------------------------------------------------
# RideMotive / Algolia API (e.g. Jim Ellis Kia)
# Bypasses 20-result proxy cap by hitting Algolia directly.
# ---------------------------------------------------------------------------

_RIDEMOTIVE_ALGOLIA_APP_ID = "G58LKO3ETJ"
_RIDEMOTIVE_ALGOLIA_KEY   = "cc3dce06acb2d9fc715bc10c9a624d80"


def _scrape_ridemotive_api(dealer_id: str, brand: str, website_url: str,
                           makes: list[str] | None = None,
                           min_year: int = 0, max_year: int | None = None) -> list[dict]:
    base = website_url.rstrip("/")
    headers = {
        "User-Agent": _UA,
        "content-type": "application/x-www-form-urlencoded",
        "x-algolia-api-key": _RIDEMOTIVE_ALGOLIA_KEY,
        "x-algolia-application-id": _RIDEMOTIVE_ALGOLIA_APP_ID,
        "referer": f"{base}/",
        "origin": base,
    }
    listings: list[dict] = []
    seen_ids: set[str] = set()  # dedup by external_id — VIN alone is unsafe when vin=None

    algolia_filters = f'car_condition:"Used" AND is_active:true AND dealer_ids:"{dealer_id}"'
    if makes:
        algolia_filters += " AND (" + " OR ".join(f'make:"{m}"' for m in makes) + ")"
    if min_year:
        algolia_filters += f" AND make_year >= {min_year}"
    if max_year:
        algolia_filters += f" AND make_year <= {max_year}"

    page = 0
    while True:
        body = json.dumps({
            "query": "",
            "filters": algolia_filters,
            "hitsPerPage": 200,
            "page": page,
        })
        r = requests.post(
            f"https://{_RIDEMOTIVE_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/production-inventory-global_price_asc/query",
            headers=headers, data=body, timeout=15
        )
        if r.status_code != 200:
            break
        data = r.json()
        hits = data.get("hits", [])
        if not hits:
            break
        for v in hits:
            normalized = _normalize_ridemotive(v, brand, base)
            if normalized and normalized["external_id"] not in seen_ids:
                listings.append(normalized)
                seen_ids.add(normalized["external_id"])
        if page >= data.get("nbPages", 1) - 1:
            break
        page += 1
    return listings


def _normalize_ridemotive(v: dict, brand: str, base: str) -> dict | None:
    price = v.get("price") or v.get("retail_price") or v.get("msrp")
    if not price:
        return None
    vin = v.get("vin") or ""
    vid = v.get("id") or v.get("objectID") or ""
    return _make_listing(
        brand, vin or v.get("stock_number", str(vid)),
        vin=vin or None,
        make=v.get("make", ""), model=v.get("model", ""),
        trim=v.get("car_trim") or None,
        year=v.get("make_year"), body_type=(v.get("category") or "").lower() or None,
        mileage=v.get("odometer"), price=int(float(price)),
        listing_url=f"{base}/inventory?id={vid}" if vid else base,
        photos_url=[],
        dealer_name=v.get("dealership") or brand,
        raw={"vin": vin, "year": v.get("make_year"), "make": v.get("make"),
             "model": v.get("model"), "trim": v.get("car_trim"), "price": price, "mileage": v.get("odometer")},
    )


# ---------------------------------------------------------------------------
# SecureOfferSites / Vue.js SSR (e.g. Hyundai of Cumming)
# SSR pages, paginated via ?page=N. Items in .standard-inventory.
# ---------------------------------------------------------------------------

async def _scrape_secureoffersites_async(inventory_url: str, brand: str) -> list[dict]:
    base = "/".join(inventory_url.split("/")[:3])
    clean = re.sub(r"[?&]page=\d+", "", inventory_url).rstrip("?&")
    sep = "&" if "?" in clean else "?"

    # V1 template: Hyundai/Kia secureoffersites with .standard-inventory cards
    _JS_EXTRACT_V1 = """() => {
        return Array.from(document.querySelectorAll('.standard-inventory')).map(el => {
            const a = el.querySelector('a[href]') || el;
            const href = a.href || a.getAttribute('href') || '';
            const vinM = href.match(/\\/viewdetails\\/used\\/([a-z0-9]{17})\\//i);
            const vin = vinM ? vinM[1].toUpperCase() : null;
            const h2 = el.querySelector('h2');
            const title = h2 ? h2.innerText.trim() : '';
            const priceEls = el.querySelectorAll('.msrp_value_custom');
            let price = null;
            for (const p of priceEls) {
                const m = p.innerText.match(/\\$([\\d,]+)/);
                if (m) { price = parseInt(m[1].replace(/,/g, '')); break; }
            }
            const text = el.innerText || '';
            const miM = text.match(/Mileage[:\\s]+([\\d,]+)\\s*Miles/i);
            const mileage = miM ? parseInt(miM[1].replace(/,/g, '')) : null;
            return { vin, title, price, mileage, href };
        });
    }"""

    # V2 template: Genesis secureoffersites with a.srp-vehicle-box cards
    _JS_EXTRACT_V2 = """() => {
        return Array.from(document.querySelectorAll('a.srp-vehicle-box')).map(el => {
            const href = el.getAttribute('href') || el.href || '';
            const vinEl = el.querySelector('[data-vin]');
            const vin = vinEl ? vinEl.getAttribute('data-vin') : null;
            const img = el.querySelector('img[title]');
            const title = img ? img.getAttribute('title') : '';
            const text = el.textContent || '';
            const priceM = text.match(/Your Price\\s*\\$([\\d,]+)/i);
            const price = priceM ? parseInt(priceM[1].replace(/,/g, '')) : null;
            const miM = text.match(/([\\d,]+)\\s*Miles/i);
            const mileage = miM ? parseInt(miM[1].replace(/,/g, '')) : null;
            return { vin, title, price, mileage, href };
        });
    }"""

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            await page.goto(f"{clean}{sep}page=1", wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(4_000)

            # Detect which template is in use
            v1_count = await page.evaluate("() => document.querySelectorAll('.standard-inventory').length")
            v2_count = await page.evaluate("() => document.querySelectorAll('a.srp-vehicle-box').length")
            js_extract = _JS_EXTRACT_V1 if v1_count > 0 else _JS_EXTRACT_V2

            max_page = 1
            if v1_count > 0:
                pag_links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('.pagination .page-link')).map(a => a.innerText.trim());
                }""")
                for t in pag_links:
                    if t.isdigit():
                        max_page = max(max_page, int(t))
            else:
                # V2: pagination links have href="...&page=N"
                pag_hrefs = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="page="]')).map(a => a.getAttribute('href') || '');
                }""")
                for href in pag_hrefs:
                    m = re.search(r'page=(\d+)', href)
                    if m:
                        max_page = max(max_page, int(m.group(1)))

            listings: list[dict] = []
            seen_vins: set[str] = set()

            for pg in range(1, max_page + 1):
                if pg > 1:
                    await page.goto(f"{clean}{sep}page={pg}", wait_until="domcontentloaded", timeout=20_000)
                    await page.wait_for_timeout(3_000)
                items = await page.evaluate(js_extract)
                for item in items:
                    vin = item.get("vin")
                    if vin and vin in seen_vins:
                        continue
                    title = item.get("title", "")
                    year_s, make, model, trim = _parse_title(title)
                    href = item.get("href", "")
                    if not href.startswith("http"):
                        href = base + href
                    listings.append(_make_listing(
                        brand, vin or re.sub(r'[^a-z0-9]', '', href.lower())[-20:],
                        vin=vin, make=make, model=model, trim=trim or None,
                        year=int(year_s) if year_s else None, body_type=None,
                        mileage=item.get("mileage"), price=item.get("price"),
                        listing_url=href or inventory_url, photos_url=[],
                        dealer_name=brand,
                        raw={"vin": vin, "title": title, "price": item.get("price"), "mileage": item.get("mileage")},
                    ))
                    if vin:
                        seen_vins.add(vin)

            return listings
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Audi One / Arcade platform (Audi dealer sites using the "One" CDN)
# ---------------------------------------------------------------------------

async def _scrape_audi_one_async(inventory_url: str, brand: str,
                                  makes: list[str] | None = None,
                                  min_year: int = 0, max_year: int | None = None) -> list[dict]:
    """Scrape Audi dealer sites running the Audi One/Arcade platform.

    Inventory loads client-side; cards appear as [class*="VehicleCard"] elements
    with [data-testid="model-name"]. Pagination via "Load more vehicles" button.
    URL pattern: https://www.audiatlanta.com/en/inventory/used/
    """
    inventory: list[dict] = []
    seen_vins: set[str] = set()
    base_url = "/".join(inventory_url.split("/")[:3])

    def _parse_card(card: dict) -> dict | None:
        vin = card.get("vin")
        if not vin:
            return None
        # model_text = "2024 Audi Q5" → year=2024, make="Audi", model="Q5"
        model_text = (card.get("model") or "").strip()
        m = re.match(r"^(\d{4})\s+(\w+)\s+(.+)$", model_text)
        if not m:
            return None
        year_str, make, model = m.group(1), m.group(2), m.group(3)
        # Trim cleanup — remove ® symbols and extra qualifiers
        raw_trim = (card.get("trim") or "").replace("®", "").replace("™", "").strip()
        trim = raw_trim or None

        mileage_str = (card.get("mileage") or "").replace(",", "")
        mileage = int(mileage_str) if mileage_str.isdigit() else None

        price_str = (card.get("price") or "").replace(",", "").replace(".", "")
        # Price comes as e.g. "6499500" from "64,995.00" with decimal stripped; use _int
        price = _int(card.get("price")) if card.get("price") else None

        if not price:
            return None

        detail_url = f"{base_url}/en/inventory/vehicle/?isdealer&market=us&vehicleId={vin}"
        stock = (card.get("stock") or "").replace("Stock #:", "").strip()

        return _make_listing(
            brand, vin,
            vin=vin, make=make, model=model, trim=trim,
            year=int(year_str), body_type=None,
            mileage=mileage, price=price,
            listing_url=detail_url,
            photos_url=[],
            dealer_name=brand,
            raw={"vin": vin, "year": year_str, "make": make, "model": model,
                 "trim": trim, "price": price, "mileage": mileage, "stock": stock},
        )

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            await page.goto(inventory_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(8_000)

            for _ in range(30):  # up to 30 "Load more" clicks
                cards_data = await page.evaluate(r"""() => {
                    const cards = [...document.querySelectorAll('[class*="VehicleCard"]')].filter(el =>
                        el.querySelector('[data-testid="model-name"]')
                    );
                    return cards.map(card => {
                        const vin = (card.querySelector('a[href*="vehicleId"]')?.href || '')
                            .match(/vehicleId=([A-Z0-9]+)/)?.[1] || null;
                        const model = card.querySelector('[data-testid="model-name"]')?.innerText?.trim() || '';
                        const trim  = card.querySelector('[data-testid="trim-name"]')?.innerText?.trim() || '';
                        const stock = card.querySelector('[data-testid="stock-number"]')?.innerText?.trim() || '';
                        const text  = card.innerText || '';
                        const mileM = text.match(/([0-9,]+)\s*mi(?:les)?/i);
                        const priceM = text.match(/\$([0-9,]+(?:\.[0-9]+)?)\b/);
                        return {
                            vin, model, trim, stock,
                            mileage: mileM ? mileM[1] : null,
                            price: priceM ? priceM[1] : null,
                        };
                    });
                }""")

                for c in cards_data:
                    if not c.get("vin") or c["vin"] in seen_vins:
                        continue
                    seen_vins.add(c["vin"])
                    listing = _parse_card(c)
                    if listing:
                        inventory.append(listing)

                # Click "Load more vehicles" to paginate
                clicked = await page.evaluate("""() => {
                    const btn = [...document.querySelectorAll('button')].find(
                        b => b.innerText?.trim().toLowerCase() === 'load more vehicles'
                    );
                    if (btn) { btn.click(); return true; }
                    return false;
                }""")
                if not clicked:
                    break
                await page.wait_for_timeout(3_000)

        finally:
            await browser.close()

    return inventory


# ---------------------------------------------------------------------------
# Playwright-based scraper (all other dealer sites)
# ---------------------------------------------------------------------------

def _extract_dv_items_js() -> str:
    return """() => {
        return Array.from(document.querySelectorAll('[data-vehicle]')).map(el => {
            try {
                const data = JSON.parse(el.getAttribute('data-vehicle'));
                const link = el.querySelector('a[href*="/inventory/"]') ||
                             el.closest('a') || el.querySelector('a');
                data._href = link ? link.href : '';
                const card = el.closest('[class*="vehicle"]') || el.closest('li') || el.parentElement;
                const miEl = card ? card.querySelector('[class*="mileage"],[class*="miles"]') : null;
                if (miEl) {
                    const m = miEl.innerText.replace(/[^0-9,]/g, '').replace(/,/g,'');
                    data._mileage = m ? parseInt(m) : null;
                }
                const img = card ? card.querySelector('img[src*="inventory"],img[src*="vehicle"],img[class*="vehicle"]') : null;
                data._photo = img ? img.src : '';
                return data;
            } catch(e) { return null; }
        }).filter(Boolean);
    }"""


async def _scroll_load_all(page) -> None:
    prev_height = -1
    for _ in range(15):
        height = await page.evaluate("() => document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)


async def _collect_dv_page(page) -> list[dict]:
    await _scroll_load_all(page)
    return await page.evaluate(_extract_dv_items_js())


async def _scrape_dealer_inventory_async(inventory_url: str, brand: str,
                                         extra_wait_ms: int = 0,
                                         makes: list[str] | None = None,
                                         min_year: int = 0,
                                         max_year: int | None = None) -> list[dict]:
    inventory_url = _build_dealer_com_url(inventory_url, makes, min_year, max_year)
    inventory_url = _build_dealeron_url(inventory_url, makes, min_year, max_year)
    base_url = "/".join(inventory_url.split("/")[:3])
    clean_url = re.sub(r"[?&](pageNum|start)=\d+", "", inventory_url).rstrip("?&")

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            await page.goto(inventory_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(6_000 + extra_wait_ms)

            # Strategy 1: DealerOn data-vehicle JSON
            initial_count = await page.evaluate(
                "() => document.querySelectorAll('[data-vehicle]').length"
            )
            if initial_count > 0:
                all_dv: list[dict] = await _collect_dv_page(page)
                # Dedup by vin-or-stock (same approach as other scrapers) — avoid using
                # VIN alone because None VINs are never added, letting duplicates slip through.
                seen_ids: set[str] = {i.get("vin") or i.get("stock", "") for i in all_dv}

                for pagenum in range(2, 21):
                    sep = "&" if "?" in clean_url else "?"
                    await page.goto(
                        f"{clean_url}{sep}pageNum={pagenum}",
                        wait_until="domcontentloaded",
                        timeout=20_000,
                    )
                    await page.wait_for_timeout(3_000 + extra_wait_ms)
                    page_count = await page.evaluate(
                        "() => document.querySelectorAll('[data-vehicle]').length"
                    )
                    if page_count == 0:
                        break
                    page_items = await _collect_dv_page(page)
                    new_items = [i for i in page_items if (i.get("vin") or i.get("stock", "")) not in seen_ids]
                    if not new_items:
                        break
                    all_dv.extend(new_items)
                    seen_ids.update(i.get("vin") or i.get("stock", "") for i in new_items)

                if all_dv:
                    # Stone Mountain VW: mileage only on detail page — fetch concurrently
                    if not any(i.get("_mileage") or i.get("mileage") or i.get("miles") for i in all_dv):
                        sem = asyncio.Semaphore(5)

                        async def _fetch_mileage(item: dict) -> None:
                            href = item.get("_href", "")
                            if not href:
                                return
                            async with sem:
                                detail_pg = None
                                try:
                                    detail_pg = await ctx.new_page()
                                    await detail_pg.goto(href, wait_until="domcontentloaded", timeout=15_000)
                                    await detail_pg.wait_for_timeout(2_000)
                                    body = await detail_pg.inner_text("body")
                                    m = (re.search(r"Mileage[:\s]*([\d,]+)", body, re.IGNORECASE)
                                         or re.search(r"Odometer[:\s]*([\d,]+)", body, re.IGNORECASE)
                                         or re.search(r"([\d,]+)\s*(?:mi|miles)\b", body, re.IGNORECASE))
                                    if m:
                                        item["_mileage"] = int(m.group(1).replace(",", ""))
                                except Exception:
                                    pass
                                finally:
                                    if detail_pg:
                                        try:
                                            await detail_pg.close()
                                        except Exception:
                                            pass

                        await asyncio.gather(*[_fetch_mileage(i) for i in all_dv])

                    return [_normalize_data_vehicle(item, brand, base_url) for item in all_dv]

            # Strategy 2: DealerOn per-attribute data-* (data-vehicle-information)
            dealeron_count = await page.evaluate(
                "() => document.querySelectorAll('[data-vehicle-information][data-vin]').length"
            )
            if dealeron_count > 0:
                await _scroll_load_all(page)
                dealeron_items = await page.evaluate("""() => {
                    function decodePrice(b64) {
                        try {
                            const text = atob(b64);
                            const m = text.match(/(?:Internet Price|Selling Price|Sale Price)[:\\s]+([\\d.]+)/i);
                            return m ? Math.round(parseFloat(m[1])) : null;
                        } catch(e) { return null; }
                    }
                    return Array.from(document.querySelectorAll('[data-vehicle-information][data-vin]')).map(el => {
                        try {
                            const vin = el.getAttribute('data-vin') || '';
                            if (!vin) return null;
                            const priceRaw = el.getAttribute('data-price');
                            const priceLib = el.getAttribute('data-pricelib') || '';
                            const priceTag = parseInt(el.getAttribute('data-dotagging-item-price') || '0');
                            // data-price=0 means "not set" on some DealerOn sites
                            const priceNum = (priceRaw !== null && priceRaw !== '' && parseInt(priceRaw) > 0)
                                ? parseInt(priceRaw)
                                : (decodePrice(priceLib) || priceTag || null);
                            const miAttr = el.getAttribute('data-dotagging-item-odometer') || '';
                            const miM = miAttr.match(/([\\d,]+)/);
                            const mileage = miM ? parseInt(miM[1].replace(/,/g,'')) : null;
                            const urlEl = el.querySelector('[data-vdp-url]') || document.querySelector('[data-vin="'+vin+'"][data-vdp-url]');
                            const href = urlEl ? urlEl.getAttribute('data-vdp-url') : '';
                            return {
                                vin,
                                make:      el.getAttribute('data-make') || '',
                                model:     el.getAttribute('data-model') || '',
                                year:      parseInt(el.getAttribute('data-year')) || null,
                                trim:      (el.getAttribute('data-trim') || '').replace(/-/g, ' '),
                                body_type: el.getAttribute('data-dotagging-item-body-type') || null,
                                price:     priceNum,
                                mileage,
                                condition: (el.getAttribute('data-vehicletype') || 'used').toLowerCase(),
                                href,
                            };
                        } catch(e) { return null; }
                    }).filter(Boolean);
                }""")
                if dealeron_items:
                    return [_normalize_dealeron_attr(item, brand, base_url) for item in dealeron_items]

            # Strategy 3: Dealer.com / AutoNation / Cox li.vehicle-card[data-uuid]
            ddcm_count = await page.evaluate(
                "() => document.querySelectorAll('li.vehicle-card[data-uuid]').length"
            )
            if ddcm_count > 0:
                all_ddcm = []
                seen_uuids = set()
                # Scrape up to 4 pages (72 listings)
                for pagenum in range(1, 5):
                    offset = (pagenum - 1) * 18
                    if pagenum > 1:
                        sep = "&" if "?" in clean_url else "?"
                        await page.goto(
                            f"{clean_url}{sep}start={offset}",
                            wait_until="domcontentloaded",
                            timeout=20_000,
                        )
                        await page.wait_for_timeout(3_000 + extra_wait_ms)
                    
                    await page.evaluate("""async () => {
                        for (const card of document.querySelectorAll('li.vehicle-card[data-uuid]')) {
                            card.scrollIntoView();
                            await new Promise(r => setTimeout(r, 150));
                        }
                    }""")
                    await page.wait_for_timeout(1000)

                    page_items = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('li.vehicle-card[data-uuid]')).map(card => {
                            try {
                                const text = card.innerText || '';
                                const html = card.innerHTML || '';
                                const uuid = card.getAttribute('data-uuid') || '';
                                const linkEl = card.querySelector('a[href]');
                                const href = linkEl ? (linkEl.href || '') : '';
                                const vinM = text.match(/VIN[:\\s]+([A-HJ-NPR-Z0-9]{17})/i)
                                          || html.match(/data-vin="([A-HJ-NPR-Z0-9]{17})"/i);
                                const vinHtmlRaw = html.match(/\\b([A-HJ-NPR-Z0-9]{17})\\b/);
                                const vin = (vinM ? vinM[1] : (vinHtmlRaw ? vinHtmlRaw[1] : '')).trim();
                                const titleEl = card.querySelector('h2,h3,[class*=vehicle-title],[class*=vehicle-name]');
                                const title = ((titleEl ? titleEl.innerText : '') || text.split('\\n').find(l => /20\\d{2}/.test(l)) || '').trim();
                                const yearM = title.match(/(20\\d{2})/);
                                const year = yearM ? parseInt(yearM[1]) : null;
                                const priceM = text.match(/(?:Vehicle Price|Selling Price|Internet Price|Our Price|Sale Price|ePrice|Dealer Price|Today[''`]?s Price)\\s*\\$([\\d,]+)/i)
                                            || text.match(/MSRP\\s*\\$([\\d,]+)/i)
                                            || text.match(/Retail Price\\s*\\$([\\d,]+)/i)
                                            || text.match(/\\$([\\d,]+)/);
                                const price = priceM ? parseInt(priceM[1].replace(/,/g,'')) : null;
                                const miM = text.match(/([\\d,]+)\\s*miles?\\b/i);
                                const mileage = miM ? parseInt(miM[1].replace(/,/g,'')) : null;
                                const isNew = /\\/new\\//i.test(href) || /^[\\s\\S]{0,150}\\bNew\\b/.test(text);
                                const condition = isNew ? 'new' : 'used';
                                return {uuid, vin: vin || null, title, year, price, mileage, condition, href};
                            } catch(e) { return null; }
                        }).filter(Boolean);
                    }""")
                    if not page_items:
                        break
                    new_items = [i for i in page_items if i["uuid"] not in seen_uuids]
                    if not new_items:
                        break
                    all_ddcm.extend(new_items)
                    seen_uuids.update(i["uuid"] for i in new_items)
                    if len(page_items) < 10:
                        break

                if all_ddcm:
                    return [_normalize_ddcm_vehicle(item, brand, base_url) for item in all_ddcm]

            # Strategy 4: Algolia InstantSearch li.ais-Hits-item (DOM text parsing)
            ais_count = await page.evaluate(
                "() => document.querySelectorAll('li.ais-Hits-item').length"
            )
            if ais_count > 0:
                all_ais: list[dict] = []

                async def _collect_ais(pg) -> list[dict]:
                    await _scroll_load_all(pg)
                    return await pg.evaluate("""() => {
                        return Array.from(document.querySelectorAll('li.ais-Hits-item')).map(el => {
                            const text = el.textContent || '';
                            const link = el.querySelector('a[href]');
                            return { text, href: link ? link.href : '' };
                        });
                    }""")

                # Dedup by external_id (always set: vin → stock → url slug).
                # VIN alone is not safe: VIN-less items have vin=None which is
                # never added to the seen set, causing the same listing to be
                # appended on every pagination page it appears on.
                seen_ids_ais: set[str] = set()

                page_items = await _collect_ais(page)
                for item in page_items:
                    parsed = _parse_ais_item(item, brand, base_url)
                    if parsed and parsed["external_id"] not in seen_ids_ais:
                        all_ais.append(parsed)
                        seen_ids_ais.add(parsed["external_id"])

                for pagenum in range(2, 21):
                    await page.wait_for_timeout(random.randint(1800, 3200))
                    sep = "&" if "?" in clean_url else "?"
                    await page.goto(f"{clean_url}{sep}page={pagenum}", wait_until="domcontentloaded", timeout=20_000)
                    await page.wait_for_timeout(3_000)
                    pg_count = await page.evaluate("() => document.querySelectorAll('li.ais-Hits-item').length")
                    if pg_count == 0:
                        break
                    page_items = await _collect_ais(page)
                    new_items = [_parse_ais_item(i, brand, base_url) for i in page_items]
                    new_items = [i for i in new_items if i and i["external_id"] not in seen_ids_ais]
                    if not new_items:
                        break
                    all_ais.extend(new_items)
                    seen_ids_ais.update(i["external_id"] for i in new_items)

                if all_ais:
                    return all_ais

            # Strategy 5: DealerInspire .result-wrap[data-vin] (mileage fetched from detail page)
            rw_count = await page.evaluate(
                "() => document.querySelectorAll('.result-wrap[data-vin]').length"
            )
            if rw_count > 0:
                cards = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('.result-wrap[data-vin]')).map(el => {
                        const text = el.textContent || '';
                        const vin = el.getAttribute('data-vin') || '';
                        const href = el.querySelector('a[href*="/inventory/"]')?.href || '';
                        const priceM = text.match(/Internet Price\\s*\\$([\\d,]+)/i)
                                    || text.match(/\\$([\\d,]+)/);
                        const price = priceM ? parseInt(priceM[1].replace(/,/g, '')) : null;
                        const titleEl = el.querySelector('h2,h3,[class*="title"]');
                        const title = titleEl ? titleEl.textContent.trim() : '';
                        return { vin, href, price, title };
                    });
                }""")
                results = []
                for card in cards:
                    vin = card.get("vin")
                    href = card.get("href", "")
                    if not href:
                        continue
                    mileage = None
                    detail_pg = None
                    try:
                        detail_pg = await ctx.new_page()
                        await detail_pg.goto(href, wait_until="domcontentloaded", timeout=20_000)
                        await detail_pg.wait_for_timeout(4000)
                        body_text = await detail_pg.inner_text("body")
                        m = re.search(r"Mileage:\s*([\d,]+)", body_text, re.IGNORECASE)
                        if m:
                            mileage = int(m.group(1).replace(",", ""))
                    except Exception as _e:
                        print(f"    [detail page] {href[-50:]}: {type(_e).__name__}: {_e}", flush=True)
                    finally:
                        if detail_pg:
                            try:
                                await detail_pg.close()
                            except Exception:
                                pass
                    title = card.get("title", "")
                    year_s, make, model, trim = _parse_title(title)
                    trim = re.sub(r"^\d+\.\d+[TtLlHhVv]\s*", "", trim).strip()
                    results.append(_make_listing(
                        brand, vin or href[-20:],
                        vin=vin or None, make=make, model=model, trim=trim or None,
                        year=_int(year_s) if year_s else None, body_type=None,
                        mileage=mileage, price=card.get("price"),
                        listing_url=href, photos_url=[],
                        dealer_name=base_url.replace("https://www.", "").replace("http://www.", ""),
                        raw=card,
                    ))
                if results:
                    return results

            # Strategy 6: JSON-LD Vehicle schema
            raw_items = await page.evaluate("""() => {
                const vehicles = [];
                for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                    try {
                        const d = JSON.parse(s.textContent);
                        if (d['@type'] === 'Vehicle' || d['@type'] === 'Car') vehicles.push(d);
                        else if (d['@graph']) vehicles.push(...d['@graph'].filter(x => x['@type'] === 'Vehicle' || x['@type'] === 'Car'));
                    } catch(e) {}
                }
                return vehicles;
            }""")
            if raw_items:
                return [_normalize_dealer_ld(item, brand, inventory_url) for item in raw_items if item.get("offers")]

            # Strategy 7: JSON-LD ItemList + DOM price/mileage (custom ASP.NET dealer sites)
            # Sites like amazingluxurycars.com embed vehicle list as ItemList with ListItems,
            # and expose price/mileage in .srp-vehicle-details / .srp-price-col DOM nodes.
            srp_items = await page.evaluate("""() => {
                // Collect ItemList entries (VIN + name embedded in URL/name)
                const ldItems = [];
                for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                    try {
                        const d = JSON.parse(s.textContent);
                        const list = Array.isArray(d) ? d : [d];
                        for (const obj of list) {
                            if (obj['@type'] === 'ItemList') {
                                ldItems.push(...(obj.itemListElement || []));
                            }
                        }
                    } catch(e) {}
                }
                if (!ldItems.length) return [];

                // Match DOM cards by position (they're in the same order as ldItems)
                const priceEls  = document.querySelectorAll('.srp-price-col');
                const detailEls = document.querySelectorAll('.srp-vehicle-details');

                return ldItems.map((item, i) => {
                    const url   = (item.url  || '').replace(/^[/][/]/, 'https://');
                    const image = (item.image || '').replace(/^[/][/]/, 'https://');
                    const vinM  = url.match(/[A-HJ-NPR-Z0-9]{17}/i);
                    const vin   = vinM ? vinM[0].toUpperCase() : null;

                    // Price from DOM card
                    const priceEl = priceEls[i];
                    const priceM  = priceEl ? priceEl.innerText.match(/\\$([\\d,]+)/) : null;
                    const price   = priceM ? parseInt(priceM[1].replace(/,/g, '')) : null;

                    // Mileage from DOM card
                    const detEl  = detailEls[i];
                    const milesM = detEl ? detEl.innerText.match(/Miles?[:\\s]+([\\d,]+)/i) : null;
                    const miles  = milesM ? parseInt(milesM[1].replace(/,/g, '')) : null;

                    return { name: item.name || '', url, image, vin, price, miles };
                }).filter(v => v.price);
            }""")
            if srp_items:
                def _parse_srp_items(raw_items):
                    out = []
                    for item in raw_items:
                        vin    = item.get("vin")
                        ext_key = vin or re.sub(r'[^a-z0-9]', '', (item.get('url') or '').lower())[-20:]
                        title  = item.get("name", "")
                        _, make, model, trim = _parse_title(title)
                        year_m = re.match(r"^(\d{4})", title.strip())
                        year   = int(year_m.group(1)) if year_m else None
                        out.append(_make_listing(
                            brand, ext_key,
                            vin=vin, make=make, model=model, trim=trim or None,
                            year=year, body_type=None,
                            mileage=item.get("miles"), price=item["price"],
                            listing_url=item.get("url") or base_url,
                            photos_url=[item["image"]] if item.get("image") else [],
                            dealer_name=brand,
                            raw={"vin": vin, "year": year, "make": make, "model": model,
                                 "trim": trim, "price": item["price"], "mileage": item.get("miles")},
                        ))
                    return out

                # Paginate: detect max page number and iterate sequentially
                seen_ids: set[str] = {r["external_id"] for r in _parse_srp_items(srp_items)}
                all_results = _parse_srp_items(srp_items)

                # Find the base paginated URL and max page from visible pagination links
                page_info = await page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a[href*="_page="]'));
                    const pages = links.map(a => {
                        const m = a.href.match(/_page=(\\d+)/);
                        return m ? parseInt(m[1]) : 0;
                    }).filter(Boolean);
                    const base = links[0] ? links[0].href.replace(/_page=\\d+/, '') : null;
                    return { base, maxPage: Math.max(0, ...pages) };
                }""")

                base_pg_url = page_info.get("base")
                max_page = page_info.get("maxPage", 0)

                for pg_num in range(2, max_page + 1):  # iterate all pages sequentially
                    pg_url = f"{base_pg_url}_page={pg_num}" if base_pg_url else None
                    if not pg_url:
                        break
                    try:
                        await page.goto(pg_url, wait_until="domcontentloaded", timeout=20000)
                        await page.wait_for_timeout(3000)
                        more = await page.evaluate("""() => {
                            const ldItems = [];
                            for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                                try {
                                    const d = JSON.parse(s.textContent);
                                    const list = Array.isArray(d) ? d : [d];
                                    for (const obj of list) {
                                        if (obj['@type'] === 'ItemList') ldItems.push(...(obj.itemListElement || []));
                                    }
                                } catch(e) {}
                            }
                            const priceEls  = document.querySelectorAll('.srp-price-col');
                            const detailEls = document.querySelectorAll('.srp-vehicle-details');
                            return ldItems.map((item, i) => {
                                const url   = (item.url  || '').replace(/^[/][/]/, 'https://');
                                const image = (item.image || '').replace(/^[/][/]/, 'https://');
                                const vinM  = url.match(/[A-HJ-NPR-Z0-9]{17}/i);
                                const vin   = vinM ? vinM[0].toUpperCase() : null;
                                const priceEl = priceEls[i];
                                const priceM  = priceEl ? priceEl.innerText.match(/\\$([\\d,]+)/) : null;
                                const price   = priceM ? parseInt(priceM[1].replace(/,/g, '')) : null;
                                const detEl  = detailEls[i];
                                const milesM = detEl ? detEl.innerText.match(/Miles?[:\\s]+([\\d,]+)/i) : null;
                                const miles  = milesM ? parseInt(milesM[1].replace(/,/g, '')) : null;
                                return { name: item.name || '', url, image, vin, price, miles };
                            }).filter(v => v.price);
                        }""")
                        page_results = _parse_srp_items(more)
                        new_items = [r for r in page_results if r["external_id"] not in seen_ids]
                        if not new_items:
                            break
                        all_results.extend(new_items)
                        seen_ids.update(r["external_id"] for r in new_items)
                    except Exception:
                        break

                if all_results:
                    return all_results

            return []
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int(val) -> int | None:
    try:
        return int(float(str(val).replace(",", "")))
    except Exception:
        return None


_KNOWN_MODELS = None


def _get_known_models() -> list[str]:
    global _KNOWN_MODELS
    if _KNOWN_MODELS is None:
        from tools.base_trims import get_allowed_models
        _KNOWN_MODELS = sorted(get_allowed_models(), key=len, reverse=True)
    return _KNOWN_MODELS


_llm_title_cache = None
_TITLE_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", ".title_cache.json")


def _load_title_cache():
    global _llm_title_cache
    if _llm_title_cache is not None:
        return
    _llm_title_cache = {}
    if "pytest" in sys.modules:
        return
    if os.path.exists(_TITLE_CACHE_FILE):
        try:
            with open(_TITLE_CACHE_FILE) as f:
                _llm_title_cache = json.load(f)
        except Exception:
            pass


def _save_title_cache():
    if _llm_title_cache is None or "pytest" in sys.modules:
        return
    try:
        with open(_TITLE_CACHE_FILE, "w") as f:
            json.dump(_llm_title_cache, f, indent=2)
    except Exception:
        pass


def _parse_title_llm(title: str) -> tuple[str, str, str, str] | None:
    """Fallback LLM parser to extract (year, make, model, trim) from messy title."""
    from tools.base_trims import get_llm_client, _VEHICLES
    client = get_llm_client()
    if client is None:
        return None

    # Compile list of target makes, models, and allowed trims
    targets = []
    for v in _VEHICLES:
        targets.append(f"- Make: {v['make']}, Model: {v['model']}, Trims: {v['trims']}")
    targets_str = "\n".join(targets)

    prompt = (
        f"You are a structured parser for car titles. Your job is to extract the year, make, model, and trim from the listing title: \"{title}\".\n\n"
        "We only track a specific list of target vehicles. Match the title to one of these targets ONLY if it is the exact same model line (e.g. do NOT map a 'Lexus GX' or 'Lexus LX' to 'Lexus NX 350h').\n"
        "Here are our target vehicles:\n"
        f"{targets_str}\n\n"
        "Return a JSON object only, with this exact schema:\n"
        '{"matched": true, "year": "YYYY", "make": "MakeName", "model": "ModelName", "trim": "TrimName"}\n'
        "Use the exact, case-sensitive Make and Model from the target list above if it matches. "
        "For trim, map to the closest target trim tier if standard abbreviations are used (e.g. 'Prem+' -> 'Premium Plus'), otherwise extract the raw trim.\n"
        "If the vehicle in the title does NOT match any of our target vehicles (different make/model or brand), return:\n"
        '{"matched": false}'
    )

    try:
        resp = client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0,
            response_format={"type": "json_object"},
        )
        answer = (resp.choices[0].message.content or "").strip()
        if not answer:
            return None
        data = json.loads(answer)
        if not data.get("matched"):
            return None
        year = str(data.get("year") or "")
        make = str(data.get("make") or "")
        model = str(data.get("model") or "")
        trim = str(data.get("trim") or "")
        if not year or not make or not model:
            return None
        return year, make, model, trim
    except Exception as e:
        print(f"  [title:llm] error parsing '{title}': {e}", flush=True)
        return None


_TITLE_PREFIXES = re.compile(
    r"^(pre[- ]owned|certified\s+(pre[- ]owned)?|used|new|cpo)\s+", re.IGNORECASE
)


_POWERTRAIN_QUALIFIERS = re.compile(r"\b(hybrid|phev|recharge|electric|plug.in|eawd)\b", re.IGNORECASE)
_POWERTRAIN_WORDS = {"hybrid", "phev", "recharge", "electric", "plug-in", "eawd", "350h", "400h", "500h"}

def _llm_model_valid_for_title(model: str, title: str) -> bool:
    """Validate an LLM-matched model against the original title.

    Two checks:
    1. If the model has a powertrain qualifier (Hybrid, PHEV, Recharge), the title must too —
       prevents 'Tucson Hybrid' matching a plain 'Tucson'.
    2. All non-powertrain words in the model name must appear in the title —
       prevents 'Tucson Hybrid' matching 'Elantra Hybrid', or 'GV70' matching 'GV80'.
    """
    model_lower = model.lower()
    title_lower = title.lower()

    # Check 1: powertrain qualifier must be in title if model has one
    if _POWERTRAIN_QUALIFIERS.search(model) and not _POWERTRAIN_QUALIFIERS.search(title):
        return False

    # Check 2: each non-powertrain word in the model name must appear in the title
    model_words = [w for w in re.split(r"[\s\-]+", model_lower) if w not in _POWERTRAIN_WORDS and len(w) > 1]
    return all(w in title_lower for w in model_words)


def _parse_title(title: str) -> tuple[str, str, str, str]:
    """Return (year, make, model, trim) from a vehicle title string."""
    cleaned = _TITLE_PREFIXES.sub("", title.strip())
    parts = cleaned.split()
    year = parts[0] if parts and parts[0].isdigit() else ""
    make = parts[1] if len(parts) > 1 else ""
    rest = " ".join(parts[2:])
    model, trim = rest, ""
    for known in _get_known_models():
        kl = known.lower()
        rl = rest.lower()
        if rl == kl or rl.startswith(kl + " "):
            model = rest[:len(known)]
            trim = rest[len(known):].strip()
            break

    # Gating check: only trigger LLM if parsing was invalid or didn't match our targets
    from tools.base_trims import get_allowed_makes
    allowed_makes = {m.lower() for m in get_allowed_makes()}
    allowed_models = {m.lower() for m in _get_known_models()}

    is_valid_parse = (
        year.isdigit() and len(year) == 4 and
        make.lower() in allowed_makes and
        model.lower() in allowed_models
    )

    if not is_valid_parse:
        _load_title_cache()
        cache_key = title.strip()
        if cache_key in _llm_title_cache:
            res = _llm_title_cache[cache_key]
            if res and _llm_model_valid_for_title(res[2], title):
                return tuple(res)
        else:
            res = _parse_title_llm(title)
            # Reject if LLM adds a powertrain qualifier not present in the original title
            # (e.g. "Tucson Hybrid" when title says just "Tucson")
            if res and not _llm_model_valid_for_title(res[2], title):
                res = None
            _llm_title_cache[cache_key] = res
            _save_title_cache()
            if res:
                print(f"  [title:llm] resolved '{title}' -> {res}", flush=True)
                return res

    return year, make, model, trim


def _normalize_dealeron_attr(item: dict, brand: str, base_url: str) -> dict:
    href = item.get("href", "")
    if href and not href.startswith("http"):
        href = base_url.rstrip("/") + "/" + href.lstrip("/")
    vin = item.get("vin") or None
    return _make_listing(
        brand, vin or href[-20:],
        vin=vin, make=item.get("make", ""), model=item.get("model", ""),
        trim=item.get("trim") or None, year=item.get("year"),
        body_type=item.get("body_type"), mileage=item.get("mileage"),
        price=item.get("price"), listing_url=href or base_url, photos_url=[],
        dealer_name=base_url.replace("https://www.", "").replace("http://www.", ""),
        condition=item.get("condition", "used"), raw=item,
    )


def _normalize_ddcm_vehicle(item: dict, brand: str, base_url: str) -> dict:
    title  = item.get("title", "")
    year_s, make, model, trim = _parse_title(title)
    vin    = item.get("vin") or None
    href   = item.get("href", "")
    if href and not href.startswith("http"):
        href = base_url + href
    return _make_listing(
        brand, item.get("uuid") or vin or href[-20:],
        vin=vin, make=make, model=model, trim=trim or None,
        year=item.get("year") or (_int(year_s) if year_s else None),
        body_type=None, mileage=item.get("mileage"), price=item.get("price"),
        listing_url=href or base_url, photos_url=[],
        dealer_name=base_url.replace("https://www.", "").replace("http://www.", ""),
        condition=item.get("condition", "used"), raw=item,
    )


def _normalize_data_vehicle(item: dict, brand: str, base_url: str) -> dict:
    vin   = item.get("vin", "")
    stock = item.get("stock", "")
    href  = item.get("_href", "")
    if not href and vin:
        slug = f"used-{item.get('year','')}-{item.get('make','').lower()}-{item.get('model','').lower()}-{vin.lower()}"
        href = f"{base_url}/inventory/{slug}/"
    body = item.get("bodystyle", "").lower()
    body = body.replace("suvs", "suv").replace("crossovers", "suv").replace("cars", "sedan") or None
    return _make_listing(
        brand, vin or stock or href[-20:],
        vin=vin or None, make=item.get("make", ""), model=item.get("model", ""),
        trim=item.get("trim"), year=_int(item.get("year")), body_type=body,
        mileage=_int(item.get("_mileage") or item.get("mileage") or item.get("miles")),
        price=_int(item.get("price") or item.get("msrp")),
        listing_url=href or f"{base_url}/inventory/",
        photos_url=[item["_photo"]] if item.get("_photo") else ([item["image"]] if item.get("image") else []),
        dealer_name=item.get("dealer_name", base_url.replace("https://www.", "")),
        dealer_phone=item.get("dealer_phone"),
        raw={k: v for k, v in item.items() if not k.startswith("_")},
    )


def _normalize_dealer_ld(item: dict, brand: str, inventory_url: str) -> dict:
    offers      = item.get("offers", {})
    price       = offers.get("price")
    listing_url = offers.get("url", inventory_url)
    id_m        = re.search(r'[/=](\d{6,})', listing_url)
    ext_key     = id_m.group(1) if id_m else re.sub(r'[^a-z0-9]', '', listing_url.lower())[-20:]
    desc        = item.get("description", "")
    trim_m      = re.search(r"(?:Used|New|Certified) \d{4} \S+ \S+ (.+?) with \d+", desc)
    return _make_listing(
        brand, ext_key,
        vin=item.get("vehicleIdentificationNumber"),
        make=item.get("manufacturer", item.get("brand", "")),
        model=item.get("model", ""),
        trim=trim_m.group(1).strip() if trim_m else None,
        year=item.get("modelDate"), body_type=None,
        mileage=item.get("mileageFromOdometer"),
        price=_int(str(price).replace(",", "")) if price else None,
        listing_url=listing_url,
        photos_url=[item["image"]] if item.get("image") else [],
        dealer_name=item.get("name", ""), raw=item,
    )


def _parse_ais_item(item: dict, brand: str, base_url: str) -> dict | None:
    text = item.get("text", "")
    href = item.get("href", "")
    if not text:
        return None

    vin_m   = re.search(r'\bVIN[:\s]+([A-HJ-NPR-Z0-9]{17})\b', text, re.IGNORECASE)
    stock_m = re.search(r'\bStock[:\s#]+([A-Z0-9]+)\b', text, re.IGNORECASE)
    vin     = vin_m.group(1) if vin_m else None
    stock   = stock_m.group(1) if stock_m else None

    title_m = re.search(r'(?:Used|New|Certified|Pre-Owned)?\s*(20\d{2})\s+(\S+)\s+(.+?)(?:\n|Mileage|$)', text, re.IGNORECASE)
    if not title_m:
        return None
    year_s, make, rest = title_m.group(1), title_m.group(2), title_m.group(3).strip()
    _, _, model, trim = _parse_title(f"{year_s} {make} {rest}")

    mi_m    = re.search(r'Mileage\s*([\d,]+)', text, re.IGNORECASE)
    mileage = int(mi_m.group(1).replace(",", "")) if mi_m else None

    price_m = re.search(r'\$([\d,]+)', text)
    price   = int(price_m.group(1).replace(",", "")) if price_m else None

    condition = "new" if re.search(r'\bNew\b', text[:80]) else "used"

    if not href.startswith("http"):
        href = base_url.rstrip("/") + "/" + href.lstrip("/")

    ext_id = vin or stock or re.sub(r'[^a-z0-9]', '', href.lower())[-20:]
    return _make_listing(
        brand, ext_id,
        vin=vin, make=make, model=model, trim=trim or None, year=int(year_s),
        body_type=None, mileage=mileage, price=price,
        listing_url=href or base_url, photos_url=[],
        dealer_name=base_url.replace("https://www.", "").replace("http://www.", ""),
        condition=condition, raw={"text": text[:300]},
    )


def _cpo_urls_for(inventory_url: str) -> list[str]:
    """Return CPO URL candidates for sites that split CPO onto a separate page.
    Not used for API-based scrapers (they already include CPO in their queries)."""
    base = "/".join(inventory_url.split("/")[:3])
    if "certified" in inventory_url.lower():
        return []
    return [f"{base}/certified-pre-owned/", f"{base}/certified/"]


# ---------------------------------------------------------------------------
# scrape_dealer / scrape_all_dealers
# ---------------------------------------------------------------------------

SITE_TIMEOUT_SECONDS = 90


def scrape_dealer(site: dict, makes: list[str] | None = None,
                  min_year: int = 0, max_year: int | None = None,
                  update_cooldown: bool = True) -> list[dict]:
    """Scrape one dealer's used + CPO inventory. Failures don't block other dealers."""
    print(f"  [dealer/{site['brand']}] {site['name']}...", flush=True)
    listings: list[dict] = []
    seen_vins: set[str] = set()
    _target_makes = {m.lower() for m in makes} if makes else set()

    def _add_used(new_listings: list[dict]) -> None:
        for l in new_listings:
            # Normalise dealer_name to the canonical site name (scrapers use generic brand strings)
            l["dealer_name"] = site["name"]
            # Pre-filter: discard non-target makes and out-of-range years before saving
            if _target_makes and (l.get("make") or "").lower() not in _target_makes:
                continue
            
            # Coerce year safely
            year = l.get("year")
            if year is not None:
                try:
                    if isinstance(year, str):
                        year = re.sub(r"[^0-9]", "", year)
                    year = int(year)
                    l["year"] = year
                except (ValueError, TypeError):
                    year = None
                    l["year"] = None

            # Coerce mileage safely
            mileage = l.get("mileage")
            if mileage is not None:
                try:
                    if isinstance(mileage, str):
                        mileage = re.sub(r"[^0-9]", "", mileage)
                    mileage = int(mileage)
                    l["mileage"] = mileage
                except (ValueError, TypeError):
                    l["mileage"] = None

            # Coerce price safely — use _int (handles decimals/commas) rather than
            # stripping all non-digits, which would inflate "29999.00" to 2999900
            price = l.get("price")
            if price is not None:
                coerced = _int(price)
                l["price"] = coerced
                price = coerced

            if year and (year < min_year or (max_year and year > max_year)):
                continue
            vin = l.get("vin")
            if vin and vin in seen_vins:
                continue
            l["condition"] = "used"
            listings.append(l)
            if vin:
                seen_vins.add(vin)

    def _run_with_timeout(coro, timeout=SITE_TIMEOUT_SECONDS):
        # asyncio.wait_for cancels the coroutine cleanly (runs finally/browser.close)
        # when the timeout fires, so the Playwright browser is actually closed.
        # Without this, the old `with ThreadPoolExecutor` pattern would call
        # shutdown(wait=True) on __exit__, blocking until the hung browser finished.
        async def _guarded():
            return await asyncio.wait_for(coro, timeout=timeout)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = executor.submit(asyncio.run, _guarded())
        try:
            return fut.result(timeout=timeout + 30)  # buffer for browser.close() after cancellation
        except (concurrent.futures.TimeoutError, asyncio.TimeoutError):
            raise concurrent.futures.TimeoutError()
        finally:
            executor.shutdown(wait=False)

    cfg = site.get("scraper_config") or {}

    if site.get("inventory_url"):
        try:
            if _is_overfuel_url(site["inventory_url"]):
                _add_used(_scrape_overfuel(
                    site["inventory_url"], site["brand"],
                    _OVERFUEL_BASE_URL  # always almcars.com — per-dealer subdomains don't host listings
                ))
            elif cfg.get("type") == "cobalt":
                _add_used(_run_with_timeout(
                    _scrape_cobalt_async(site["inventory_url"], site["brand"], makes, min_year, max_year),
                    timeout=150,
                ))
            elif cfg.get("type") == "cobalt_dom":
                _add_used(_run_with_timeout(
                    _scrape_cobalt_dom_async(site["inventory_url"], site["brand"], makes, min_year, max_year),
                    timeout=150,
                ))
                # Probe certified inventory on DDC sites (separate URL path)
                base = "/".join(site["inventory_url"].split("/")[:3])
                cpo_url = f"{base}/certified-inventory/index.htm"
                try:
                    cpo_listings = _run_with_timeout(
                        _scrape_cobalt_dom_async(cpo_url, site["brand"], makes, min_year, max_year),
                        timeout=60,
                    )
                    if cpo_listings:
                        print(f"    CPO: {len(cpo_listings)} listings at /certified-inventory/", flush=True)
                        _add_used(cpo_listings)
                except Exception:
                    pass
            elif cfg.get("type") == "dealerinspire_algolia":
                _add_used(_scrape_dealerinspire_algolia(
                    cfg["app_id"], cfg["api_key"], cfg["index"], site["brand"],
                    site.get("website_url", site["inventory_url"]),
                    makes, min_year, max_year,
                ))
            elif cfg.get("type") == "carscommerce":
                _add_used(_scrape_carscommerce_api(
                    cfg["dealer_id"], cfg["api_key"], site["brand"],
                    site.get("website_url", site["inventory_url"]),
                    type_slugs=cfg.get("type_slugs"),
                    allowed_states=cfg.get("allowed_states"),
                ))
            elif cfg.get("type") == "ridemotive":
                _add_used(_scrape_ridemotive_api(
                    cfg["dealer_id"], site["brand"],
                    site.get("website_url", site["inventory_url"]),
                    makes, min_year, max_year,
                ))
            elif cfg.get("type") == "secureoffersites":
                _add_used(_run_with_timeout(
                    _scrape_secureoffersites_async(site["inventory_url"], site["brand"])
                ))
            elif cfg.get("type") == "dealeron_cosmos":
                _add_used(_run_with_timeout(
                    _scrape_dealeron_cosmos_async(site["inventory_url"], site["brand"],
                                                   makes=makes, min_year=min_year, max_year=max_year,
                                                   cosmos_path=cfg.get("cosmos_path"))
                ))
            elif cfg.get("type") == "audi_one":
                _add_used(_run_with_timeout(
                    _scrape_audi_one_async(site["inventory_url"], site["brand"],
                                           makes=makes, min_year=min_year, max_year=max_year),
                    timeout=150,
                ))
            else:
                _add_used(_run_with_timeout(
                    _scrape_dealer_inventory_async(site["inventory_url"], site["brand"],
                                                   extra_wait_ms=cfg.get("extra_wait_ms", 0),
                                                   makes=makes, min_year=min_year, max_year=max_year)
                ))
        except concurrent.futures.TimeoutError:
            print(f"    timed out after {SITE_TIMEOUT_SECONDS}s — skipping", flush=True)
        except Exception as e:
            print(f"    used inventory error: {e}", flush=True)

        # CPO: only probe for Playwright-based sites — API scrapers already include CPO
        if not cfg.get("type"):
            for cpo_url in _cpo_urls_for(site["inventory_url"]):
                try:
                    cpo_listings = _run_with_timeout(
                        _scrape_dealer_inventory_async(cpo_url, site["brand"],
                                                       makes=makes, min_year=min_year, max_year=max_year)
                    )
                    if cpo_listings:
                        print(f"    CPO: {len(cpo_listings)} listings at {cpo_url.replace(site['inventory_url'].rsplit('/',2)[0],'')}", flush=True)
                        _add_used(cpo_listings)
                        break
                except (concurrent.futures.TimeoutError, Exception):
                    pass

    if update_cooldown:
        execute("UPDATE dealer_sites SET last_scraped_at = now() WHERE id = %s", (site["id"],))
    print(f"    {len(listings)} listings found", flush=True)
    return listings


DEALER_COOLDOWN_HOURS = 6


def scrape_all_dealers(makes: list[str] | None = None,
                       min_year: int = 0, max_year: int | None = None,
                       force: bool = False, update_cooldown: bool = True) -> list[dict]:
    """Scrape active dealer sites. force=True bypasses the 6h cooldown (used by training_collector).
    update_cooldown=False skips stamping last_scraped_at so the next sourcing refresh isn't blocked."""
    # ridemotive sites use website_url + dealer_id and have no inventory_url — include them too
    _active_filter = """is_active = true
               AND (inventory_url IS NOT NULL OR scraper_config->>'type' = 'ridemotive')"""
    if force:
        sites = fetch_all(
            f"""SELECT id, brand, name, inventory_url, last_scraped_at, scraper_config, website_url
               FROM dealer_sites WHERE {_active_filter}"""
        )
        print(f"  [dealer] {len(sites)} site(s) (force mode — ignoring cooldown)", flush=True)
    else:
        sites = fetch_all(
            f"""SELECT id, brand, name, inventory_url, last_scraped_at, scraper_config, website_url
               FROM dealer_sites WHERE {_active_filter}
               AND (last_scraped_at IS NULL
                    OR last_scraped_at < now() - INTERVAL '%s hours')""",
            (DEALER_COOLDOWN_HOURS,),
        )
        if not sites:
            print(f"  [dealer] All sites scraped within last {DEALER_COOLDOWN_HOURS}h — skipping", flush=True)
            return []
        print(f"  [dealer] {len(sites)} site(s) due for scrape (cooldown: {DEALER_COOLDOWN_HOURS}h)", flush=True)

    all_listings: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(scrape_dealer, site, makes, min_year, max_year, update_cooldown): site for site in sites}
        for fut in concurrent.futures.as_completed(futures):
            site = futures[fut]
            try:
                all_listings.extend(fut.result())
            except Exception as e:
                print(f"  [dealer/{site['name']}] unexpected error: {e}", flush=True)
    return all_listings
