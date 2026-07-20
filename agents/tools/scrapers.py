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

# Shared client — instantiated once when the first LLM call is made, reused thereafter.
_llm_client = None

def _get_llm_client():
    global _llm_client
    if _llm_client is None and _OpenAI is not None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if api_key:
            _llm_client = _OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    return _llm_client

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

async def _scrape_carvana_async(zip_code, makes, max_price, max_mileage, min_year, vehicles=None, max_year=None):
    # Carvana is nationwide — no radius param. Model filter uses base name only;
    # hybrid vs non-hybrid is indistinguishable in the URL so _is_valid() handles post-scrape.
    make_str  = "&".join(f"make={m.lower()}" for m in makes)
    model_str = _base_model_params(vehicles, "model")
    year_max_str = f"&maxYear={max_year}" if max_year else ""
    url = (
        f"https://www.carvana.com/cars?"
        f"zip={zip_code}&maxPrice={max_price}&maxMileage={max_mileage}"
        f"&minYear={min_year}{year_max_str}&{make_str}"
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
            items = []
            prev_count = -1
            for _ in range(6):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2_000)
                items = await _fetch_carvana_items(page)
                if len(items) == prev_count:
                    break
                prev_count = len(items)
            return _parse_carvana_items(items)
        finally:
            await browser.close()


def scrape_carvana(zip_code, makes, max_price, max_mileage, min_year, vehicles=None, max_year=None):
    return _run(_scrape_carvana_async(zip_code, makes, max_price, max_mileage, min_year, vehicles=vehicles, max_year=max_year))


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


async def _scrape_carmax_async(zip_code, makes, max_price, max_mileage, min_year, radius=500, vehicles=None, max_year=None):
    make_str  = "&".join(f"makes[]={m}" for m in makes)
    # CarMax model codes use base model only (no "Hybrid" suffix in URL)
    model_str = _base_model_params(vehicles, "models[]", lower=False)
    year_max_str = f"&maxYear={max_year}" if max_year else ""
    url = (
        f"https://www.carmax.com/cars?"
        f"zip={zip_code}&maxPrice={max_price}&maxMileage={max_mileage}"
        f"&minYear={min_year}{year_max_str}&radius={radius}&{make_str}"
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
            tiles: list[dict] = []
            prev_count = -1
            for _ in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2_000)
                tiles = await _fetch_tiles(page)
                if len(tiles) == prev_count:
                    break
                prev_count = len(tiles)
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


def scrape_carmax(zip_code, makes, max_price, max_mileage, min_year, radius=500, vehicles=None, max_year=None):
    return _run(_scrape_carmax_async(zip_code, makes, max_price, max_mileage, min_year, radius=radius, vehicles=vehicles, max_year=max_year))


# ---------------------------------------------------------------------------
# CarGurus — DOM extraction from srp-listing-blade elements
# Listings are SSR'd into [data-cg-ft="srp-listing-blade"].
# We scrape sedan and SUV pages separately since CarGurus uses body-group URLs.
# Bonus: captures CarGurus deal rating (Great/Good/Fair Deal) stored in raw.
# ---------------------------------------------------------------------------

def _parse_cargurus_blade(text: str, href: str, expected_model: str | None = None, expected_make: str | None = None) -> dict | None:
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
        # Reject if the base model name doesn't match (e.g. "UX" tile on NX 350h page).
        expected_base = expected_model.split()[0].lower()
        actual_base = parts[2].lower() if len(parts) > 2 else ""
        if actual_base != expected_base:
            return None
        # If the page is a Hybrid model but the listing text doesn't say Hybrid,
        # this is a sponsored cross-variant listing — reject it.
        if "hybrid" in expected_model.lower() and "hybrid" not in name_line.lower() and "hybrid" not in text.lower():
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
        "body_type": "suv",
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


def scrape_cargurus(zip_code, makes, max_price, max_mileage, min_year, radius=100, max_year=None,
                    clean_history=False, local: bool = True, vehicles=None):
    from tools.base_trims import _VEHICLES

    # When a vehicle allowlist is provided, restrict scraping to those models so we
    # don't open CarGurus pages for every model in a stage that only needs a subset.
    if vehicles is not None:
        vehicle_keys = {(m.lower(), mo.lower()) for m, mo in vehicles}
        pool = [v for v in _VEHICLES if (v["make"].lower(), v["model"].lower()) in vehicle_keys]
    else:
        pool = _VEHICLES

    search_path_vehicles = [v for v in pool if v.get("cargurus_search_path")]
    if not search_path_vehicles:
        return []
    return _run(_scrape_cargurus_search_all_async(
        search_path_vehicles, zip_code, min_year, max_year, radius=radius, local=local,
        clean_history=clean_history,
    ))


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
    radius: int = 50000, local: bool = False, clean_history: bool = False,
) -> list[dict]:
    """Scrape all search-path vehicles with up to 3 parallel browser contexts."""
    delivery     = "false" if local else "true"
    # Explicitly set shouldFilterByState — CarGurus defaults to true when param is absent,
    # so omitting it silently limits nationwide training scrapes to the local state.
    state_filter = "&shouldFilterByState=true" if local else "&shouldFilterByState=false"
    # nonShippableBaseline ensures non-delivery dealers are included in nationwide (training) searches.
    non_shippable = "" if local else "&nonShippableBaseline=73"
    history_opts = _CARGURUS_HISTORY_FILTER
    if clean_history:
        history_opts += ",NO_ACCIDENTS"
    sem = asyncio.Semaphore(3)

    async def _scrape_vehicle(browser, v: dict) -> list[dict]:
        make    = v["make"]
        model   = v["model"]
        full_path = v["cargurus_search_path"]
        # For nationwide (training) scrapes, use only the make/model-level path segments
        # (no trim suffixes) so CarGurus returns all trim variants. Trim filtering happens
        # post-scrape in _insert via is_trim_allowed_for_training. For local sourcing scrapes
        # the full trim-specific paths are kept to reduce noise.
        if not local:
            segments = [s for s in full_path.split(",") if s.count("/") <= 1]
            path = ",".join(segments) if segments else full_path
        else:
            path = full_path
        eff_min = max(min_year, v.get("min_year", min_year))
        v_max = v.get("max_year")
        if max_year is not None:
            eff_max = min(v_max, max_year) if v_max is not None else max_year
        else:
            eff_max = v_max
        base = (
            f"https://www.cargurus.com/search?srpVariation=DEFAULT_SEARCH"
            f"&zip={zip_code}&distance={radius}"
            f"&makeModelTrimPaths={urllib.parse.quote(path, safe='/')}"
            f"&isDeliveryEnabled={delivery}&startYear={eff_min}"
            f"&vehicleHistoryOptions={history_opts}"
            + (f"&endYear={eff_max}" if eff_max else "")
            + state_filter
            + non_shippable
        )
        listings: list[dict] = []
        seen_ids: set[str]   = set()
        page_size: int | None = None

        async with sem:
            ctx = await browser.new_context(
                user_agent=random.choice(_UA_POOL),
                viewport=random.choice(_VIEWPORT_POOL),
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await ctx.new_page()
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
                    new_on_page = 0
                    for tile in tiles:
                        parsed = _parse_cargurus_blade(tile["text"], tile["href"], model, make)
                        if parsed and parsed["external_id"] not in seen_ids:
                            seen_ids.add(parsed["external_id"])
                            listings.append(parsed)
                            new_on_page += 1
                    if page_size is None:
                        page_size = len(tiles) or 1
                    if len(tiles) < page_size or new_on_page == 0 or page_num == 100:
                        break
                print(f"    [cargurus/{model}] {len(listings)} listings", flush=True)
            except Exception as e:
                print(f"    [cargurus/{model}] error: {e}", flush=True)
            finally:
                await ctx.close()

        return listings

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            results = await asyncio.gather(*[_scrape_vehicle(browser, v) for v in vehicles])
        finally:
            await browser.close()

    return [listing for model_listings in results for listing in model_listings]


# ---------------------------------------------------------------------------
# Cars.com — vehicle-card DOM extraction
# ---------------------------------------------------------------------------


async def _scrape_carscom_async(zip_code, makes, max_price, max_mileage, min_year, radius=100,
                                 vehicles=None, clean_history=False, max_year=None):
    seen_slugs: set[str] = set()
    model_params, make_params = [], []
    make_seen: set[str] = set()
    for make, model in (vehicles or []):
        model_slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
        slug = f"{make.lower()}-{model_slug}"
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            model_params.append(f"models[]={slug}")
        if make.lower() not in make_seen:
            make_seen.add(make.lower())
            make_params.append(f"makes[]={make.lower()}")

    history_filter = "&no_accidents=true&clean_title=true&personal_use=true" if clean_history else ""
    year_max_filter = f"&year_max={max_year}" if max_year else ""
    url = (
        f"https://www.cars.com/shopping/results/?stock_type=used"
        f"&{'&'.join(make_params)}&{'&'.join(model_params)}"
        f"&zip={zip_code}&maximum_distance={radius}"
        f"&maximum_price={max_price}&maximum_mileage={max_mileage}"
        f"&year_min={min_year}{year_max_filter}&sort=best_match_desc{history_filter}"
    )

    async def _fetch_cards(page):
        # data-listing-id moved to child FUSE-SAVE elements inside FUSE-CARD parent
        # that holds data-vehicle-details. Walk up from each [data-listing-id] to find details.
        return await page.evaluate("""() => {
            const seen = new Set();
            const results = [];
            for (const el of document.querySelectorAll('[data-listing-id]')) {
                const id = el.getAttribute('data-listing-id');
                if (seen.has(id)) continue;
                seen.add(id);
                let detailsEl = el.hasAttribute('data-vehicle-details') ? el : null;
                if (!detailsEl) {
                    let p = el.parentElement;
                    for (let i = 0; i < 5 && p; i++) {
                        if (p.hasAttribute('data-vehicle-details')) { detailsEl = p; break; }
                        p = p.parentElement;
                    }
                }
                if (!detailsEl) detailsEl = el.querySelector('[data-vehicle-details]');
                if (!detailsEl) continue;
                results.push({
                    id,
                    details: detailsEl.getAttribute('data-vehicle-details'),
                    text: (detailsEl.innerText || el.innerText || '').slice(0, 300)
                });
            }
            return results.filter(c => c.details);
        }""")

    async def _scrape_carscom_page(page, page_url: str) -> list[dict]:
        """Load one Cars.com results page, scroll to load lazy cards, return raw card dicts."""
        await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(8_000)
        await _check_blocked(page, "carscom")
        cards: list[dict] = []
        prev_count = -1
        for _ in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2_500)
            cards = await _fetch_cards(page)
            if len(cards) == prev_count:
                break
            prev_count = len(cards)
        return cards

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
                    print(f"  [carscom] page {page_num} error: {e}", flush=True)
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
                    drive_type = d.get("driveType") or ""
                    raw_text = " ".join(filter(None, [card["text"][:400], drive_type]))
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
                        "raw": {"text": raw_text, "deal_rating": deal_rating},
                    })
                except Exception as e:
                    print(f"  [carscom] card parse error (id={lid}): {e}", flush=True)
                    continue
            return listings
        finally:
            await browser.close()


def scrape_carscom(zip_code, makes, max_price, max_mileage, min_year, radius=100, vehicles=None, clean_history=False, max_year=None):
    return _run(_scrape_carscom_async(zip_code, makes, max_price, max_mileage, min_year, radius=radius, vehicles=vehicles, clean_history=clean_history, max_year=max_year))


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

# Patterns that confirm a lemon/buyback title — each uses a negative lookbehind for "no "
# to avoid false positives from Carfax widgets that print "No Lemon Law Buyback Reported".
_LEMON_PATTERNS = [
    r"lemon\s+title",
    r"reported\s+as\s+lemon",
    r"(?<!no\s)(?<!not\s)manufacturer\s+buyback",
    r"(?<!no\s)buyback\s+vehicle",
    r"mechanical\s+or\s+manufacturing\s+defect",
]


def _parse_history(text: str) -> dict:
    """Extract accident count, title status, owner count, mileage, and drivetrain from page text."""
    from tools.scoring import detect_drivetrain
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

    # Drivetrain: VDP "Features & specs" section typically contains e.g.
    # "Front-wheel Drive drivetrain" or "All-Wheel Drive drivetrain"
    dt = detect_drivetrain(None, None, text_lower)
    drivetrain = dt if dt != "unknown" else None

    return {
        "accident_count": accident_count,
        "title_status": title_status,
        "owner_count": owner_count,
        "mileage": mileage,
        "drivetrain": drivetrain,
    }


_ENRICH_CONCURRENCY  = 5   # parallel detail-page fetches per browser session
_ENRICH_TIMEOUT      = 600  # hard cap on the whole enrichment batch (10 min)
_CARSCOM_PAGE_SIZE   = 24  # Cars.com shows 24 listings per page
_CARSCOM_MAX_PAGES   = 25  # cap at 600 listings per run (all 9 models combined)


_INSIGHTS_KEYWORDS = re.compile(
    r"certif|package|price.?drop|reduced|was \$|owner|service.?record|garage|carfax|autocheck",
    re.IGNORECASE,
)

def _focus_listing_text(text: str, limit: int = 2000) -> str:
    """Return a focused excerpt of page innerText for LLM insight extraction.

    Collapses whitespace, finds all keyword matches, and builds a window that
    covers from the first match (minus 200 chars) to 2000 chars past the last
    match. Falls back to the first `limit` chars when no keyword is found.
    Uses 2000 chars (up from 1500) to reduce risk of cutting off late-listed
    option packages on verbose dealer pages.
    """
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    matches = list(_INSIGHTS_KEYWORDS.finditer(text))
    if matches:
        start = max(0, matches[0].start() - 200)
        end   = min(len(text), matches[-1].end() + limit)
        excerpt = text[start:end]
        # If the span is larger than 2×limit, prefer the last-match anchor
        # (most dealer pages front-load boilerplate before option packages).
        if len(excerpt) > 2 * limit:
            start = max(0, matches[-1].start() - 400)
            excerpt = text[start : start + limit]
        return excerpt
    return text[:limit]


def _extract_insights_llm(text: str, allowed_trims: list[str] | None = None) -> dict:
    """Call LLM to extract qualitative insights from a listing detail page.
    Returns: {cpo, packages, price_drop, owner_notes} + optional {trim}.

    When allowed_trims is provided, the LLM is asked to match the trim to one
    of those exact strings. The result is validated post-LLM: any returned trim
    that isn't literally in allowed_trims is discarded before returning.
    """
    client = _get_llm_client()
    if client is None:
        return {}
    try:
        insights_excerpt = _focus_listing_text(text)

        if allowed_trims:
            trims_str = ", ".join(f'"{t}"' for t in allowed_trims)
            trim_field = ',"trim":str|null'
            trim_instruction = (
                f'trim: from the vehicle title/header match to EXACTLY one of: {trims_str}. '
                f'Return the verbatim string from that list, or null if not clearly present. '
            )
            # Prepend the top of the page so the listing title is always in context
            title_text = text[:800].strip()
            full_excerpt = f"{title_text}\n---\n{insights_excerpt}"
        else:
            trim_field = ""
            trim_instruction = ""
            full_excerpt = insights_excerpt

        prompt = (
            f'Return JSON only: {{"cpo":bool,"packages":[str],"price_drop":int|null,"owner_notes":str|null{trim_field}}}\n'
            "cpo=true only if explicitly CPO/Certified Pre-Owned. "
            "packages=named option packages (empty array if none). "
            "price_drop=dollar reduction if stated. "
            "owner_notes=short string for notable condition/history (null if none). "
            f"{trim_instruction}"
            "Only explicit facts, no inference.\n\n"
            f"{full_excerpt}"
        )
        resp = client.chat.completions.create(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            timeout=15,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        result = {
            "cpo":         bool(data.get("cpo", False)),
            "packages":    [str(p) for p in (data.get("packages") or [])],
            "price_drop":  int(data["price_drop"]) if data.get("price_drop") else None,
            "owner_notes": str(data["owner_notes"])[:80] if data.get("owner_notes") else None,
        }
        # Hard guardrail: only accept the trim if it's literally in the allowed list.
        # This prevents the LLM from inventing or abbreviating a trim name.
        if allowed_trims and data.get("trim"):
            allowed_lower = {t.lower(): t for t in allowed_trims}
            validated = allowed_lower.get(str(data["trim"]).lower())
            if validated:
                result["trim"] = validated
        return result
    except Exception as e:
        print(f"    [insights] LLM extraction failed: {e}", flush=True)
        return {}


async def _enrich_detail_async(pairs: list[tuple[str, str, list[str] | None]], results: dict) -> None:
    """Fetch detail pages concurrently (up to _ENRICH_CONCURRENCY at a time).

    Writes into the caller-supplied `results` dict so partial data survives
    an asyncio.TimeoutError cancellation from the outer wait_for wrapper.
    pairs: (ext_id, url, allowed_trims) — allowed_trims passed to LLM for trim correction.
    """
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def _fetch_one(ctx, ext_id: str, url: str, allowed_trims: list[str] | None) -> None:
        async with sem:
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await page.wait_for_timeout(2_000)
                text = await page.evaluate("() => document.body.innerText")
                data = _parse_history(text)
                insights = await asyncio.to_thread(_extract_insights_llm, text, allowed_trims)
                data["insights"] = insights
                results[ext_id] = data
                trim_note = f" trim={insights['trim']!r}" if insights.get("trim") else ""
                print(f"    [{ext_id}] accidents={data['accident_count']} "
                      f"title={data['title_status']} owners={data['owner_count']} "
                      f"mileage={data['mileage']} drivetrain={data.get('drivetrain')}"
                      + (f" cpo={insights.get('cpo')} pkgs={len(insights.get('packages',[]))}{trim_note}" if insights else ""),
                      flush=True)
            except Exception as e:
                print(f"    [{ext_id}] detail fetch error: {e}", flush=True)
            finally:
                await page.close()

    async with Stealth().use_async(async_playwright()) as p:
        browser, ctx = await _stealth_browser(p)
        try:
            await asyncio.gather(*[_fetch_one(ctx, ext_id, url, allowed_trims) for ext_id, url, allowed_trims in pairs])
        finally:
            await browser.close()


def enrich_listings_from_detail(listings: list[dict]) -> list[dict]:
    """Fetch detail pages for new listings and enrich with vehicle history data."""
    from tools.base_trims import _find_vehicle, is_trim_allowed

    def _allowed_trims(make: str, model: str) -> list[str] | None:
        v = _find_vehicle(make, model)
        return v.get("trims") if v else None

    pairs = [
        (str(listing["external_id"]), listing["listing_url"],
         _allowed_trims(listing.get("make", ""), listing.get("model", "")))
        for listing in listings if listing.get("listing_url")
    ]
    if not pairs:
        return listings
    enriched: dict[str, dict] = {}
    try:
        asyncio.run(asyncio.wait_for(_enrich_detail_async(pairs, enriched), timeout=_ENRICH_TIMEOUT))
    except asyncio.TimeoutError:
        print(f"  [enrich] session timed out after {_ENRICH_TIMEOUT}s "
              f"— {len(enriched)}/{len(pairs)} fetched, using partial results", flush=True)
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
        # Upgrade "unknown" drivetrain using the explicit drivetrain line from the VDP
        # "Features & specs" section (e.g. "Front-wheel Drive drivetrain").
        if data.get("drivetrain") and listing.get("drivetrain", "unknown") == "unknown":
            listing["drivetrain"] = data["drivetrain"]
        if data.get("insights"):
            existing = listing.get("insights") or {}
            llm = data["insights"]
            # Merge: scraper-detected CPO (URL path / certified field) takes precedence
            listing["insights"] = {**llm, "cpo": llm.get("cpo") or existing.get("cpo") or False}
            # Trim correction: only override when the current trim is absent or fails the
            # allowlist check. Never overwrite a trim that already passed validation —
            # the scraper's structured data is more reliable than LLM for correct trims.
            detail_trim = llm.get("trim")
            if detail_trim:
                make = listing.get("make", "")
                model = listing.get("model", "")
                current_trim = listing.get("trim")
                if not current_trim or not is_trim_allowed(make, model, current_trim):
                    listing["trim"] = detail_trim
                    print(f"    [enrich:trim] {make} {model}: {current_trim!r} → {detail_trim!r}", flush=True)
    return listings
