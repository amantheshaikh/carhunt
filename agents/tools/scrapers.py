"""
Playwright + stealth scrapers for CarGurus, Cars.com, CarMax, and Carvana.

Each scraper returns raw listing dicts; the sourcing agent handles dedup,
validation, detail-page enrichment, and DB persistence.

Note: AutoTrader is blocked by Akamai Bot Manager (TLS fingerprint detection).
      Edmunds returns 403 at the network level. Both are excluded.
"""
import asyncio
import json
import os
import random
import re
import urllib.parse

try:
    from openai import OpenAI as _OpenAI
except ImportError:
    _OpenAI = None

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Rotate across current Chrome versions and common screen sizes.
# Picked once per browser session so the fingerprint is internally consistent
# but varies across runs — reduces the "same bot every time" signal.
_UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]

_VIEWPORT_POOL = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]


async def _human_delay(page, min_ms: int = 800, max_ms: int = 2500) -> None:
    """Random human-like pause."""
    await page.wait_for_timeout(random.randint(min_ms, max_ms))


async def _gradual_scroll(page, step: int = 400) -> None:
    """Scroll down the page gradually with jitter, like a human reading."""
    total_height = await page.evaluate("document.body.scrollHeight")
    pos = 0
    while pos < total_height:
        pos += step + random.randint(-80, 80)
        pos = min(pos, total_height)
        await page.evaluate(f"window.scrollTo(0, {pos})")
        await page.wait_for_timeout(random.randint(80, 250))
    # Slight scroll back up — common human pattern
    await page.evaluate(f"window.scrollTo(0, {max(0, pos - random.randint(100, 300))})")
    await page.wait_for_timeout(random.randint(200, 500))


_BLOCK_SIGNALS = [
    "verify you are human",
    "access denied",
    "enable javascript and cookies to continue",
    "cf-browser-verification",
    "checking your browser",
    "please complete the security check",
    "are you a robot",
    "unusual traffic from your computer",
    "automated queries",
    "bot protection",
]


async def _check_blocked(page, source: str) -> None:
    """Raise RuntimeError if the page looks like a bot-detection or CAPTCHA screen.
    Called after every page load for CarGurus and Cars.com — the two sources most
    likely to serve empty results silently when they detect automation."""
    text = (await page.evaluate("() => document.body.innerText || ''")).lower()
    for signal in _BLOCK_SIGNALS:
        if signal in text:
            raise RuntimeError(f"bot-detection page served (matched: '{signal}')")


def _run(coro):
    return asyncio.run(coro)


def _base_model_params(vehicles: list | None, param: str, lower: bool = True) -> str:
    """Build deduplicated base-model query params (e.g. model=tucson or models[]=Tucson).
    Uses the first word of each model name since sites don't distinguish Hybrid variants in URLs."""
    seen: set[str] = set()
    parts = []
    for _, m in (vehicles or []):
        base = m.split()[0]
        key  = base.lower() if lower else base
        if key not in seen:
            seen.add(key)
            parts.append(f"{param}={base.lower() if lower else base}")
    return "&".join(parts)


async def _stealth_browser(playwright):
    ua       = random.choice(_UA_POOL)
    viewport = random.choice(_VIEWPORT_POOL)
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        viewport=viewport,
        user_agent=ua,
        locale="en-US",
        timezone_id="America/New_York",
    )
    return browser, context


# ---------------------------------------------------------------------------
# Carvana
# ---------------------------------------------------------------------------

async def _scrape_carvana_async(zip_code, makes, max_price, max_mileage, min_year, vehicles=None):
    # Carvana is nationwide — no radius param. Model filter uses base name only;
    # hybrid vs non-hybrid is indistinguishable in the URL so _is_valid() handles post-scrape.
    make_str  = "&".join(f"make={m.lower()}" for m in makes)
    model_str = _base_model_params(vehicles, "model")
    url = (
        f"https://www.carvana.com/cars?"
        f"zip={zip_code}&maxPrice={max_price}&maxMileage={max_mileage}"
        f"&minYear={min_year}&{make_str}"
        + (f"&{model_str}" if model_str else "")
    )

    def _parse_carvana_items(raw_items):
        listings = []
        for item in raw_items:
            try:
                offers = item.get("offers", {})
                price = offers.get("price")
                listing_url = offers.get("url", "")
                vid_m = re.search(r"/vehicle/(\d+)", listing_url)
                external_id = vid_m.group(1) if vid_m else str(item.get("sku", ""))
                desc = item.get("description", "")
                trim_m = re.search(r"(?:Used|Certified(?: Used)?|Pre-Owned)?\s*\d{4}\s+\S+\s+\S+\s+(.+?)\s+with\s+\d+", desc, re.IGNORECASE)
                trim = trim_m.group(1).strip() if trim_m else None
                # Fallback: extract trim from URL slug (e.g. /vehicle/2025-toyota-rav4-xle-premium-...)
                if not trim and listing_url:
                    slug_m = re.search(r"/vehicle/\d{4}-[a-z]+-[a-z0-9]+-([a-z0-9][a-z0-9-]+?)-[A-Z0-9]{17}", listing_url)
                    if slug_m:
                        trim = slug_m.group(1).replace("-", " ").title()
                if not price or not external_id:
                    continue
                listings.append({
                    "source": "carvana",
                    "condition": "used",
                    "external_id": external_id,
                    "vin": item.get("vehicleIdentificationNumber"),
                    "make": item.get("manufacturer", item.get("brand", "")),
                    "model": item.get("model", ""),
                    "trim": trim,
                    "year": item.get("modelDate"),
                    "body_type": None,
                    "mileage": item.get("mileageFromOdometer"),
                    "price": int(price),
                    "dealer_name": "Carvana",
                    "dealer_phone": None,
                    "dealer_email": None,
                    "listing_url": listing_url or f"https://www.carvana.com/vehicle/{external_id}",
                    "photos_url": [item["image"]] if item.get("image") else [],
                    "title_status": "clean",
                    "accident_count": 0,
                    "raw": item,
                })
            except Exception:
                continue
        return listings

    async def _fetch_carvana_items(page):
        return await page.evaluate("""() => {
            const scripts = document.querySelectorAll('script[data-testid="vehicle-ld"]');
            return Array.from(scripts).map(s => {
                try { return JSON.parse(s.textContent); } catch(e) { return null; }
            }).filter(Boolean);
        }""")

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(8_000)
            # Scroll to load all lazy-loaded listings
            prev_count = 0
            for _ in range(6):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2_000)
                items = await _fetch_carvana_items(page)
                if len(items) == prev_count:
                    break
                prev_count = len(items)
            raw_items = await _fetch_carvana_items(page)
            return _parse_carvana_items(raw_items)
        finally:
            await browser.close()


def scrape_carvana(zip_code, makes, max_price, max_mileage, min_year, vehicles=None):
    return _run(_scrape_carvana_async(zip_code, makes, max_price, max_mileage, min_year, vehicles=vehicles))


# ---------------------------------------------------------------------------
# CarMax
# ---------------------------------------------------------------------------

def _parse_carmax_tile(text: str, stock_num: str, min_year: int) -> dict | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name_line = next((l for l in lines if re.match(r"^\d{4} ", l)), None)
    if not name_line:
        return None
    parts = name_line.split()
    if len(parts) < 3:
        return None
    year = int(parts[0])
    if year < min_year:
        return None
    make = parts[1]
    model = parts[2]
    trim = " ".join(parts[3:]) if len(parts) > 3 else None
    price_m = re.search(r"\$([0-9,]+)\*?", text)
    if not price_m:
        return None
    price = int(price_m.group(1).replace(",", ""))
    mi_m = re.search(r"([\d,]+)K mi|([\d,]+) mi", text)
    if mi_m:
        raw = (mi_m.group(1) or mi_m.group(2)).replace(",", "")
        mileage = int(raw) * 1000 if mi_m.group(1) else int(raw)
    else:
        mileage = None
    return {
        "source": "carmax",
        "condition": "used",
        "external_id": stock_num,
        "vin": None,
        "make": make,
        "model": model,
        "trim": trim,
        "year": year,
        "body_type": None,
        "mileage": mileage,
        "price": price,
        "dealer_name": "CarMax",
        "dealer_phone": None,
        "dealer_email": None,
        "listing_url": f"https://www.carmax.com/car/{stock_num}",
        "photos_url": [],
        "title_status": "clean",
        "accident_count": 0,
        "raw": {"text": text},
    }


async def _scrape_carmax_async(zip_code, makes, max_price, max_mileage, min_year, radius=500, vehicles=None):
    make_str  = "&".join(f"makes[]={m}" for m in makes)
    # CarMax model codes use base model only (no "Hybrid" suffix in URL)
    model_str = _base_model_params(vehicles, "models[]", lower=False)
    url = (
        f"https://www.carmax.com/cars?"
        f"zip={zip_code}&maxPrice={max_price}&maxMileage={max_mileage}"
        f"&minYear={min_year}&radius={radius}&{make_str}"
        + (f"&{model_str}" if model_str else "")
    )

    async def _fetch_tiles(page):
        return await page.evaluate("""() => {
            const els = document.querySelectorAll('[data-id]');
            return Array.from(els).map(el => ({
                stock: el.getAttribute('data-id'),
                text: el.innerText
            })).filter(t => t.stock && /\\d{4} /.test(t.text));
        }""")

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(8_000)
            # Scroll to trigger lazy loading
            prev_count = 0
            for _ in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2_000)
                tiles = await _fetch_tiles(page)
                if len(tiles) == prev_count:
                    break
                prev_count = len(tiles)
            tiles = await _fetch_tiles(page)
            listings = []
            seen = set()
            for tile in tiles:
                stock = tile["stock"]
                if stock in seen:
                    continue
                seen.add(stock)
                parsed = _parse_carmax_tile(tile["text"], stock, min_year)
                if parsed:
                    listings.append(parsed)
            return listings
        finally:
            await browser.close()


def scrape_carmax(zip_code, makes, max_price, max_mileage, min_year, radius=500, vehicles=None):
    return _run(_scrape_carmax_async(zip_code, makes, max_price, max_mileage, min_year, radius=radius, vehicles=vehicles))


# ---------------------------------------------------------------------------
# CarGurus — DOM extraction from srp-listing-blade elements
# Listings are SSR'd into [data-cg-ft="srp-listing-blade"].
# We scrape sedan and SUV pages separately since CarGurus uses body-group URLs.
# Bonus: captures CarGurus deal rating (Great/Good/Fair Deal) stored in raw.
# ---------------------------------------------------------------------------

def _parse_cargurus_blade(text: str, href: str, body_type: str, expected_model: str | None = None, expected_make: str | None = None) -> dict | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    id_m = re.search(r"/details/(\d+)", href)
    if not id_m:
        return None
    external_id = id_m.group(1)

    # First line matching "YYYY Make Model" (skip "Learn more" lines)
    name_line = next((l for l in lines if re.match(r"^\d{4} ", l) and "Learn more" not in l), None)
    if not name_line:
        return None
    parts = name_line.split()
    if len(parts) < 3:
        return None
    year, make = int(parts[0]), parts[1]

    # Reject sponsored cross-brand listings: if the blade's make doesn't match the
    # page's expected make, this is an ad for a different vehicle (e.g. Elantra on RAV4 page).
    if expected_make and make.lower() != expected_make.lower():
        return None

    # Model extraction: on a model-specific page, trust the page label over the blade text.
    # e.g. d2318 (RAV4 Hybrid page) may show "RAV4 XLE" — we still store it as "RAV4 Hybrid".
    rest = " ".join(parts[2:])
    model = parts[2]
    if expected_model:
        # If the page is a Hybrid model but the listing text doesn't say Hybrid,
        # this is a sponsored cross-variant listing — reject it.
        if "hybrid" in expected_model.lower() and "hybrid" not in name_line.lower():
            return None
        model = expected_model
        em = expected_model.strip()
        if rest.lower().startswith(em.lower()):
            trim_from_name = rest[len(em):].strip() or None
        else:
            trim_from_name = " ".join(parts[3:]) or None
    else:
        trim_from_name = " ".join(parts[3:]) or None

    # Trim is the line after "Learn more about this ..."
    learn_idx = next((i for i, l in enumerate(lines) if "Learn more" in l), None)
    trim = None
    if learn_idx is not None and learn_idx + 1 < len(lines):
        candidate = lines[learn_idx + 1]
        if not re.match(r"^\d{4} ", candidate) and len(candidate) < 60:
            trim = candidate
    if not trim:
        trim = trim_from_name

    # "X mi away" — dealer distance from search zip; "X,XXX mi" — odometer mileage
    # Distance appears as "202 mi away"; mileage as "4,860 mi" (with comma or standalone)
    distance_miles = None
    dist_m = re.search(r"([\d,]+)\s+mi\s+away", text, re.IGNORECASE)
    if dist_m:
        distance_miles = int(dist_m.group(1).replace(",", ""))

    # Mileage: first "N mi" NOT followed by "away"
    mileage = None
    for mm in re.finditer(r"([\d,]+)\s*mi\b", text):
        candidate = int(mm.group(1).replace(",", ""))
        after = text[mm.end():mm.end() + 10].lower()
        if "away" not in after and candidate <= 300_000:
            mileage = candidate
            break

    # Find the listing price: first dollar amount ≥$5,000 (avoids monthly payment figures like $149/mo)
    price = None
    for pm in re.finditer(r"\$([\d,]+)", text):
        candidate = int(pm.group(1).replace(",", ""))
        if candidate >= 5000:
            price = candidate
            break
    if price is None:
        return None
    # CarGurus rolls shipping into the displayed price for store-transfer listings.
    # Subtract it so we store the actual vehicle price.
    shipping_m = re.search(r"Price includes \$([\d,]+) shipping", text, re.IGNORECASE)
    if shipping_m:
        price -= int(shipping_m.group(1).replace(",", ""))

    deal_rating = next(
        (r for r in ("Great Deal", "Good Deal", "Fair Deal", "High Price", "Overpriced") if r in text),
        None
    )

    loc_m = re.search(r"([A-Za-z\s]+,\s*[A-Z]{2})\n", text)
    dealer_name = loc_m.group(1).strip() if loc_m else None

    return {
        "source": "cargurus",
        "condition": "used",
        "external_id": external_id,
        "vin": None,
        "make": make,
        "model": model,
        "trim": trim,
        "year": year,
        "body_type": body_type,
        "mileage": mileage,
        "distance_miles": distance_miles,
        "price": price,
        "dealer_name": dealer_name,
        "dealer_phone": None,
        "dealer_email": None,
        "listing_url": f"https://www.cargurus.com/details/{external_id}",
        "photos_url": [],
        "title_status": "clean",
        "accident_count": 0,
        "raw": {"text": text[:500], "deal_rating": deal_rating},
    }


_BLADE_SEL = '[data-cg-ft="srp-listing-blade"],[data-cg-ft="srp-listing-blade-sponsored"]'
_PAGE_SIZE = 24   # CarGurus shows 24 per page
_MAX_PAGES = 6    # cap at 144 results per model


async def _extract_blades(page) -> list[dict]:
    return await page.evaluate(f"""() => {{
        return Array.from(document.querySelectorAll('{_BLADE_SEL}')).map(blade => {{
            const link = blade.closest('a') || blade.parentElement?.closest('a');
            return {{ text: blade.innerText || '', href: link ? link.href : '' }};
        }});
    }}""")


async def _scrape_cargurus_model(page, base_url: str, expected_make: str, expected_model: str) -> list[dict]:
    """Scrape all pages for one model-specific CarGurus URL.

    Uses startListing URL parameter for pagination rather than DOM selectors —
    CarGurus migrated from <a> links to <button> elements for page navigation,
    breaking every CSS/aria selector approach. URL construction is selector-proof.
    """
    listings: list[dict] = []
    seen_ids: set[str] = set()
    _parsed_base = urllib.parse.urlparse(base_url)
    _base_params = urllib.parse.parse_qs(_parsed_base.query, keep_blank_values=True)

    for page_num in range(_MAX_PAGES):
        if page_num == 0:
            url = base_url
        else:
            params = {**_base_params, "startListing": [str(page_num * _PAGE_SIZE)]}
            url = urllib.parse.urlunparse(
                _parsed_base._replace(query=urllib.parse.urlencode(params, doseq=True))
            )

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await _human_delay(page, 6000, 10000)
        await _check_blocked(page, "cargurus")
        # Dismiss "Only show listings in GA?" or similar location modals that block blade rendering
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const dismiss = btns.find(b => /no,? thanks|not now|dismiss|cancel/i.test(b.innerText.trim()));
            if (dismiss) dismiss.click();
        }""")
        await page.wait_for_timeout(2000)
        await _gradual_scroll(page)
        await page.wait_for_timeout(2000)

        blades = await _extract_blades(page)
        parsed_on_page = 0
        for blade in blades:
            parsed = _parse_cargurus_blade(blade["text"], blade["href"], "suv", expected_model, expected_make)
            if parsed:
                parsed_on_page += 1
                if parsed["external_id"] not in seen_ids:
                    seen_ids.add(parsed["external_id"])
                    listings.append(parsed)

        if parsed_on_page < _PAGE_SIZE or page_num == _MAX_PAGES - 1:
            break

    return listings


async def _scrape_cargurus_async(zip_code, makes, max_price, max_mileage, min_year, condition="used",
                                  radius=100, max_year=None, clean_history=False):
    from tools.base_trims import get_cargurus_ids
    cond_prefix = "n" if condition == "new" else "l"
    cond_slug   = "New" if condition == "new" else "Used"
    year_range  = f"&startYear={min_year}" + (f"&endYear={max_year}" if max_year else "")
    mi_filter   = "" if condition == "new" else f"&maxMileage={max_mileage}"
    history_filter = "&noAccidents=true&personalUseOnly=true" if clean_history else ""
    base_params = f"zip={zip_code}&distance={radius}&maxPrice={max_price}{mi_filter}{year_range}{history_filter}"

    cg_ids = get_cargurus_ids()
    if cg_ids:
        # pages_to_scrape: (url, make, model)
        pages_to_scrape = [
            (f"https://www.cargurus.com/Cars/{cond_prefix}-{cond_slug}-{make}-{model.replace(' ', '-')}-{cg_id}?{base_params}", make, model)
            for make, model, cg_id in cg_ids
        ]
    else:
        pages_to_scrape = [
            (f"https://www.cargurus.com/Cars/l-{cond_slug}-SUV-Crossover-bg7?{base_params}", "", "suv"),
        ]

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            all_listings, seen_ids = [], set()
            source_name = "cargurus_new" if condition == "new" else "cargurus"
            for url, exp_make, label in pages_to_scrape:
                try:
                    results = await _scrape_cargurus_model(page, url, exp_make, label)
                    page_new = 0
                    for l in results:
                        if l["external_id"] not in seen_ids:
                            seen_ids.add(l["external_id"])
                            l["source"] = source_name
                            l["condition"] = condition
                            if condition == "new":
                                l["mileage"] = 0
                                l["title_status"] = "clean"
                                l["accident_count"] = 0
                            all_listings.append(l)
                            page_new += 1
                    print(f"    [cargurus/{label}] {page_new} listings")
                except Exception as e:
                    print(f"    [cargurus/{label}] error: {e}")
            return all_listings
        finally:
            await browser.close()


def scrape_cargurus(zip_code, makes, max_price, max_mileage, min_year, radius=100, max_year=None,
                    clean_history=False, local: bool = True):
    from tools.base_trims import _VEHICLES

    # Keep full vehicle dicts to avoid re-looking them up inside the loop.
    search_path_vehicles = [v for v in _VEHICLES if v.get("cargurus_search_path") and v.get("cargurus_id")]
    legacy_makes = [v["make"] for v in _VEHICLES if not v.get("cargurus_search_path") and v.get("cargurus_id")]

    all_listings: list[dict] = []

    # New format: one browser session shared across all models (avoids 9× open/close overhead).
    if search_path_vehicles:
        all_listings.extend(_run(_scrape_cargurus_search_all_async(
            search_path_vehicles, zip_code, min_year, max_year, radius=radius, local=local,
        )))

    # Legacy format: models without a search path
    if legacy_makes:
        legacy = _run(_scrape_cargurus_async(zip_code, legacy_makes, max_price, max_mileage, min_year,
                                              radius=radius, max_year=max_year, clean_history=True))
        all_listings.extend(legacy)

    return all_listings


# ---------------------------------------------------------------------------
# CarGurus new search format — uses data-testid="srp-listing-tile" DOM elements
# and makeModelTrimPaths for trim-level filtering + vehicleHistoryOptions for
# comprehensive clean-history enforcement (better than noAccidents=true alone).
# ---------------------------------------------------------------------------

_CARGURUS_HISTORY_FILTER = "CLEAN_TITLE,LEMON_FREE,NON_SALVAGE,NON_FLEET,NO_THEFT_HISTORY,UNDAMAGED_FRAME"


async def _dismiss_cargurus_modal(page) -> None:
    """Dismiss the 'Only show listings in GA?' modal if present."""
    await page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const dismiss = btns.find(b => /no,? thanks|not now|dismiss/i.test(b.innerText.trim()));
        if (dismiss) dismiss.click();
    }""")


async def _scrape_cargurus_search_all_async(
    vehicles: list[dict], zip_code: str, min_year: int, max_year: int | None,
    radius: int = 50000, local: bool = False,
) -> list[dict]:
    """Scrape all search-path vehicles in ONE shared browser session."""
    import urllib.parse as _up
    delivery    = "false" if local else "true"
    state_filter = "&shouldFilterByState=true" if local else ""
    all_listings: list[dict] = []

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            for v in vehicles:
                make  = v["make"]
                model = v["model"]
                path  = v["cargurus_search_path"]
                eff_min = max(min_year, v.get("min_year", min_year))
                eff_max = v.get("max_year") or max_year
                base = (
                    f"https://www.cargurus.com/search?srpVariation=DEFAULT_SEARCH"
                    f"&zip={zip_code}&distance={radius}"
                    f"&makeModelTrimPaths={_up.quote(path, safe='/')}"
                    f"&isDeliveryEnabled={delivery}&startYear={eff_min}"
                    f"&vehicleHistoryOptions={_CARGURUS_HISTORY_FILTER}"
                    + (f"&endYear={eff_max}" if eff_max else "")
                    + state_filter
                )
                listings: list[dict] = []
                seen_ids: set[str]   = set()
                page_size: int | None = None
                try:
                    for page_num in range(1, 101):
                        url = base + (f"&page={page_num}" if page_num > 1 else "")
                        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        await _human_delay(page, 3000, 5000)
                        await _check_blocked(page, "cargurus")
                        await _dismiss_cargurus_modal(page)
                        await page.wait_for_timeout(1000)
                        await _gradual_scroll(page)
                        await page.wait_for_timeout(1500)
                        tiles = await page.evaluate("""() =>
                            Array.from(document.querySelectorAll('[data-testid="srp-listing-tile"]')).map(el => {
                                const link = el.querySelector('a[href*="/details/"],a[href*="/vehicledetail/"]');
                                return { text: el.innerText || '', href: link ? link.href : '' };
                            })
                        """)
                        for tile in tiles:
                            parsed = _parse_cargurus_blade(tile["text"], tile["href"], "suv", model, make)
                            if parsed and parsed["external_id"] not in seen_ids:
                                seen_ids.add(parsed["external_id"])
                                listings.append(parsed)
                        if page_size is None:
                            page_size = len(tiles) or 1
                        if len(tiles) < page_size or page_num == 100:
                            break
                    print(f"    [cargurus/{model}] {len(listings)} listings")
                    all_listings.extend(listings)
                except Exception as e:
                    print(f"    [cargurus/{model}] error: {e}")
        finally:
            await browser.close()

    return all_listings

async def _scrape_cargurus_search_async(search_path: str, make: str, model: str,
                                         zip_code: str, min_year: int, max_year: int | None,
                                         radius: int = 50000, local: bool = False) -> list[dict]:
    """Scrape a single model using the new CarGurus search format (public training helper)."""
    v = {"make": make, "model": model, "cargurus_search_path": search_path,
         "min_year": min_year, "max_year": max_year}
    return await _scrape_cargurus_search_all_async([v], zip_code, min_year, max_year,
                                                    radius=radius, local=local)

    return listings


def scrape_cargurus_search(search_path: str, make: str, model: str, zip_code: str,
                            min_year: int, max_year: int | None = None,
                            radius: int = 50000, local: bool = False) -> list[dict]:
    return _run(_scrape_cargurus_search_async(search_path, make, model, zip_code,
                                              min_year, max_year, radius, local=local))


# ---------------------------------------------------------------------------
# Cars.com — vehicle-card DOM extraction
# ---------------------------------------------------------------------------



async def _scrape_carscom_async(zip_code, makes, max_price, max_mileage, min_year, radius=100,
                                 vehicles=None, clean_history=False):
    seen_slugs: set[str] = set()
    model_params, make_params = [], []
    make_seen: set[str] = set()
    for make, model in (vehicles or []):
        slug = f"{make.lower()}-{model.lower().replace(' ', '_')}"
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            model_params.append(f"models[]={slug}")
        if make.lower() not in make_seen:
            make_seen.add(make.lower())
            make_params.append(f"makes[]={make.lower()}")

    history_filter = "&no_accidents=true&clean_title=true&personal_use=true" if clean_history else ""
    url = (
        f"https://www.cars.com/shopping/results/?stock_type=used"
        f"&{'&'.join(make_params)}&{'&'.join(model_params)}"
        f"&zip={zip_code}&distance={radius}"
        f"&maximum_price={max_price}&maximum_mileage={max_mileage}"
        f"&year_min={min_year}&sort=best_match_desc{history_filter}"
    )

    async def _fetch_cards(page):
        return await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('[data-listing-id][data-vehicle-details]')).map(el => ({
                id: el.getAttribute('data-listing-id'),
                details: el.getAttribute('data-vehicle-details'),
                text: (el.innerText || '').slice(0, 300)
            })).filter(c => c.id && c.details);
        }""")

    async def _scrape_carscom_page(page, page_url: str) -> list[dict]:
        """Load one Cars.com results page, scroll to load lazy cards, return raw card dicts."""
        await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(8_000)
        await _check_blocked(page, "carscom")
        prev_count = 0
        for _ in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2_500)
            cards = await _fetch_cards(page)
            if len(cards) == prev_count:
                break
            prev_count = len(cards)
        return await _fetch_cards(page)

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        page = await ctx.new_page()
        try:
            await page.goto("https://www.cars.com/", wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)

            all_cards = await _scrape_carscom_page(page, url)

            # Paginate: Cars.com supports &page=N — continue until fewer than a full page returned
            for page_num in range(2, _CARSCOM_MAX_PAGES + 1):
                if len(all_cards) < (page_num - 1) * _CARSCOM_PAGE_SIZE:
                    break  # previous page was short — no more results
                try:
                    sep = "&" if "?" in url else "?"
                    next_cards = await _scrape_carscom_page(page, f"{url}{sep}page={page_num}")
                    new_ids = {c["id"] for c in next_cards} - {c["id"] for c in all_cards}
                    if not new_ids:
                        break
                    all_cards.extend(c for c in next_cards if c["id"] in new_ids)
                    if len(next_cards) < _CARSCOM_PAGE_SIZE:
                        break  # last page
                except Exception as e:
                    print(f"  [carscom] page {page_num} error: {e}")
                    break

            listings: list[dict] = []
            seen: set[str] = set()
            for card in all_cards:
                lid = card["id"]
                if lid in seen:
                    continue
                seen.add(lid)
                try:
                    d = json.loads(card["details"])
                    price = int(d.get("price") or 0)
                    if price < 5000:
                        continue
                    mileage = int(d.get("mileage") or 0) or None
                    deal_rating = next(
                        (r for r in ("Great Deal", "Good Deal", "Fair Deal", "High Price", "Overpriced")
                         if r in card["text"]), None
                    )
                    listings.append({
                        "source": "carscom",
                        "condition": "used",
                        "external_id": lid,
                        "vin": d.get("vin"),
                        "make": d.get("make", ""),
                        "model": d.get("model", ""),
                        "trim": d.get("trim"),
                        "year": int(d.get("year") or 0) or None,
                        "body_type": (d.get("bodyStyle") or "").lower() or None,
                        "mileage": mileage,
                        "price": price,
                        "dealer_name": None,
                        "dealer_phone": None,
                        "dealer_email": None,
                        "listing_url": f"https://www.cars.com/vehicledetail/{lid}/",
                        "photos_url": [],
                        "title_status": "clean",
                        "accident_count": 0,
                        "raw": {"text": card["text"][:400], "deal_rating": deal_rating},
                    })
                except Exception as e:
                    print(f"  [carscom] card parse error (id={lid}): {e}")
                    continue
            return listings
        finally:
            await browser.close()


def scrape_carscom(zip_code, makes, max_price, max_mileage, min_year, radius=100, vehicles=None, clean_history=False):
    return _run(_scrape_carscom_async(zip_code, makes, max_price, max_mileage, min_year, radius=radius, vehicles=vehicles, clean_history=clean_history))


# ---------------------------------------------------------------------------
# Detail page enrichment — fetches vehicle history for new listings
# ---------------------------------------------------------------------------

# Accident patterns: (regex, value_if_matched)
_ACCIDENT_PATTERNS: list[tuple[str, int | None]] = [
    (r"(\d+)\s+accidents?\s+reported",              None),  # None → extract group(1) as int
    (r"no\s+accidents?\s+(?:or\s+\S+\s+)?reported", 0),     # "no accidents reported" OR "no accidents or damage reported"
    (r"no\s+reported\s+accidents?",                  0),
]

_FLEET_PATTERNS = [
    r"work\s+fleet", r"fleet\s+vehicle", r"rental\s+vehicle",
    r"commercial\s+use", r"taxi\s+use", r"livery\s+use",
]

_SALVAGE_PATTERNS = [
    r"salvage\s+title", r"rebuilt\s+title", r"flood\s+damage",
]

_LEMON_PATTERNS = [
    r"lemon\s+title", r"reported\s+as\s+lemon", r"lemon\s+law",
    r"manufacturer\s+buyback", r"buyback\s+vehicle",
    r"mechanical\s+or\s+manufacturing\s+defect",
]


def _parse_history(text: str) -> dict:
    """Extract accident count, title status, owner count, and mileage from page text."""
    text_lower = text.lower()

    accident_count = None
    for pattern, fixed_value in _ACCIDENT_PATTERNS:
        m = re.search(pattern, text_lower, re.IGNORECASE)
        if m:
            accident_count = int(m.group(1)) if fixed_value is None else fixed_value
            break

    title_status = "clean"
    if any(re.search(p, text_lower) for p in _LEMON_PATTERNS):
        title_status = "lemon"
    elif any(re.search(p, text_lower) for p in _SALVAGE_PATTERNS):
        title_status = "salvage"
    elif any(re.search(p, text_lower) for p in _FLEET_PATTERNS):
        title_status = "fleet"

    owner_m = re.search(r"(\d+)\s+previous\s+owner", text_lower)
    owner_count = int(owner_m.group(1)) if owner_m else None

    # Mileage: look for patterns like "28,344 miles", "28344 mi", "Mileage: 28,344"
    mileage = None
    for pat in [
        r"([\d,]+)\s*miles?\b",
        r"mileage[:\s]+([\d,]+)",
        r"odometer[:\s]+([\d,]+)",
    ]:
        mm = re.search(pat, text_lower)
        if mm:
            candidate = int(mm.group(1).replace(",", ""))
            # Sanity check: reasonable mileage range (exclude years like 2025, zip codes, etc.)
            if 100 <= candidate <= 300_000:
                mileage = candidate
                break

    return {
        "accident_count": accident_count,
        "title_status": title_status,
        "owner_count": owner_count,
        "mileage": mileage,
    }


_ENRICH_CONCURRENCY  = 3   # parallel detail-page fetches per browser session
_ENRICH_TIMEOUT      = 600  # hard cap on the whole enrichment batch (10 min)
_CARSCOM_PAGE_SIZE   = 24  # Cars.com shows 24 listings per page
_CARSCOM_MAX_PAGES   = 10  # cap at 240 listings per run


def _extract_insights_llm(text: str) -> dict:
    """Call LLM to extract qualitative insights from a listing detail page.
    Returns: {cpo, packages, price_drop, owner_notes}
    """
    if _OpenAI is None or not os.environ.get("OPENROUTER_API_KEY"):
        return {}
    try:
        client = _OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        prompt = (
            "Extract information from this used car listing page. "
            "Return ONLY valid JSON with these fields:\n"
            '- "cpo": true/false (is it explicitly Certified Pre-Owned or CPO?)\n'
            '- "packages": array of option package names mentioned (e.g. ["Premium Package", "M Sport Package"]). Empty array if none.\n'
            '- "price_drop": integer dollars if a price reduction is mentioned, else null\n'
            '- "owner_notes": one short string if notable owner/condition info (e.g. "1-owner, all service records"), else null\n'
            "Only include what is explicitly stated. Do not infer.\n\n"
            f"Listing text:\n{text[:3000]}"
        )
        resp = client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            timeout=15,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "cpo":         bool(data.get("cpo", False)),
            "packages":    [str(p) for p in (data.get("packages") or [])],
            "price_drop":  int(data["price_drop"]) if data.get("price_drop") else None,
            "owner_notes": str(data["owner_notes"])[:80] if data.get("owner_notes") else None,
        }
    except Exception as e:
        print(f"    [insights] LLM extraction failed: {e}")
        return {}


async def _enrich_detail_async(pairs: list[tuple[str, str]], results: dict) -> None:
    """Fetch detail pages concurrently (up to _ENRICH_CONCURRENCY at a time).

    Writes into the caller-supplied `results` dict so partial data survives
    an asyncio.TimeoutError cancellation from the outer wait_for wrapper.
    """
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def _fetch_one(ctx, ext_id: str, url: str) -> None:
        async with sem:
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await page.wait_for_timeout(2_000)
                text = await page.evaluate("() => document.body.innerText")
                data = _parse_history(text)
                # Run LLM insight extraction in a thread so it doesn't block the event loop
                insights = await asyncio.to_thread(_extract_insights_llm, text)
                data["insights"] = insights
                results[ext_id] = data
                print(f"    [{ext_id}] accidents={data['accident_count']} "
                      f"title={data['title_status']} owners={data['owner_count']} "
                      f"mileage={data['mileage']}"
                      + (f" cpo={insights.get('cpo')} pkgs={len(insights.get('packages',[]))}" if insights else ""))
            except Exception as e:
                print(f"    [{ext_id}] detail fetch error: {e}")
            finally:
                await page.close()

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        try:
            await asyncio.gather(*[_fetch_one(ctx, ext_id, url) for ext_id, url in pairs])
        finally:
            await browser.close()


def enrich_listings_from_detail(listings: list[dict]) -> list[dict]:
    """Fetch detail pages for new listings and enrich with vehicle history data."""
    pairs = [
        (str(listing["external_id"]), listing["listing_url"])
        for listing in listings if listing.get("listing_url")
    ]
    if not pairs:
        return listings
    enriched: dict[str, dict] = {}
    try:
        asyncio.run(asyncio.wait_for(_enrich_detail_async(pairs, enriched), timeout=_ENRICH_TIMEOUT))
    except asyncio.TimeoutError:
        print(f"  [enrich] session timed out after {_ENRICH_TIMEOUT}s "
              f"— {len(enriched)}/{len(pairs)} fetched, using partial results")
    for listing in listings:
        data = enriched.get(str(listing["external_id"]), {})
        if data.get("accident_count") is not None:
            listing["accident_count"] = data["accident_count"]
        if data.get("title_status"):
            listing["title_status"] = data["title_status"]
        if data.get("owner_count") is not None:
            listing["owner_count"] = data["owner_count"]
        # Use detail-page mileage only when the search/inventory page didn't provide one.
        # Explicit None check — truthy check would treat mileage=0 as "not provided".
        if data.get("mileage") is not None and listing.get("mileage") is None:
            listing["mileage"] = data["mileage"]
        if data.get("insights"):
            listing["insights"] = data["insights"]
    return listings
