import argparse
import json
import datetime
import re
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from dotenv import load_dotenv
from firecrawl import Firecrawl
from google import genai
from playwright.sync_api import sync_playwright
from restaurant_menu_agent.config import RestaurantSummary, MENU_PROMPT, REGULAR_MENU_PROMPT

load_dotenv()
MAPPING = {
    "Monday": "PONEDJELJAK",
    "Tuesday": "UTORAK",
    "Wednesday": "SRIJEDA",
    "Thursday": "ČETVRTAK",
    "Friday": "PETAK",
    "Saturday": "SUBOTA",
    "Sunday": "NEDJELJA",
}

HARDCODED_RESTAURANTS = [
    {"name": "Fakin", "url": "https://pivovara-medvedgrad.hr/fakin/gableci"},
    {
        "name": "Lobby",
        "url": f"https://lobby-eurotower.skubacz.pl/restauracja/lobby-eurotower#menu-dnevni-meni-{datetime.datetime.now().strftime('%A').lower()}",
    },
    {"name": "Cassandra", "url": "https://www.cassandra.hr/restoran-cassandra/dnevni-jelovnici/"},
]

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NEXT_DAY_MARKERS = [
    "ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja", "novo u ponudi",
]

MENU_LINK_PATTERN = re.compile(
    r"menu|jelovnik|meni|ponuda|cijen|hrana|food|order|pizza|burger|sushi|carte|gablec|dnevni",
    re.I,
)
REGULAR_MENU_LINK_PATTERN = re.compile(
    r"menu|jelovnik|meni|ponuda|cijen|hrana|food|order|pizza|burger|sushi|carte|a-la-carte",
    re.I,
)
GABLEC_URL_PATTERN = re.compile(
    r"gablec|dnevni[\s_-]?(jelovnik|meni|menu)|business[\s_-]?lunch",
    re.I,
)
GABLEC_CONTENT_PATTERN = re.compile(
    r"\bgablec(i)?\b|dnevni[\s-]?(jelovnik|meni)\b|ručak[\s-]?ponuda|business[\s-]?lunch",
    re.I,
)
ALACARTE_CONTENT_PATTERN = re.compile(
    r"a[\s-]?la[\s-]?carte|menu a la carte|standardn[iy]?\s+jelovnik",
    re.I,
)
MENU_PATHS = [
    "/menu-a-la-carte",
    "/menu-a-la-carte/",
    "/dnevni-menu",
    "/dnevni-menu/",
    "/menu",
    "/menu/",
    "/jelovnik",
    "/jelovnik/",
    "/meni",
    "/meni/",
    "/hrana",
    "/ponuda",
    "/cijene",
    "/food",
    "/order",
]
REGULAR_MENU_PATHS = [path for path in MENU_PATHS if "dnevni" not in path]
MAX_MENU_CHARS = 50000
MAX_PAGES_TO_TRY = 8
MIN_PARSED_ITEMS_TO_SKIP_BROWSER = 10
NAV_LINE_PATTERN = re.compile(
    r"^(dnevni menu|menu a la carte|početna|kontakt|gal(erija)?|pića)$",
    re.I,
)
SECTION_HEADER_PATTERN = re.compile(
    r"^(menu|hladna|topla|tjestenina|mesni|desert|prilozi|salat|predjela|gušti|pića|dodaci|fino|domaći|sve cijene)",
    re.I,
)
PRICE_ONLY_PATTERN = re.compile(r"^[\d]+[.,][\d]{2}\s*(€|EUR|kn|HRK)?\s*$", re.I)
INLINE_PRICE_PATTERN = re.compile(r"^(.+?)\s+([\d]+[.,][\d]{2})\s*(€|EUR|kn|HRK)?\s*$", re.I)

firecrawl = Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY"))
gemini_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def _generate_structured_json(prompt: str, max_output_tokens: int = 4096) -> str:
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": RestaurantSummary,
            "max_output_tokens": max_output_tokens,
        },
    )
    return response.text


def normalize_price(price: str) -> str:
    if not price:
        return price
    cleaned = re.sub(r"\s+", " ", price.replace("\u00a0", " ").strip())
    match = re.search(r"([\d]+[.,][\d]{2})\s*(€|EUR|eur|kn|HRK)?", cleaned, re.I)
    if match:
        amount = match.group(1).replace(".", ",")
        currency = (match.group(2) or "€").upper()
        if currency in {"KN", "HRK"}:
            return f"{amount} kn"
        return f"{amount} €"
    return cleaned


def _price_count(text: str) -> int:
    return len(re.findall(r"[\d]+[.,][\d]{2}\s*(?:€|EUR|kn|HRK)?", text, re.I))


def _is_zero_price(price: str) -> bool:
    match = re.search(r"([\d]+[.,][\d]{2})", price)
    if not match:
        return False
    amount = match.group(1).replace(",", ".")
    try:
        return float(amount) == 0.0
    except ValueError:
        return False


def _priced_dishes(data: dict) -> list[dict]:
    priced = []
    for dish in data.get("specials", []):
        price = dish.get("price", "")
        if (
            dish.get("item_name")
            and price
            and re.search(r"[\d]+[.,][\d]{2}", price)
            and not _is_zero_price(price)
        ):
            priced.append(dish)
    return priced


def is_gablec_menu(text: str, url: str = "") -> bool:
    """True when the scraped page is a daily/gablec menu, not a standard a la carte jelovnik."""
    sample = text[:10000].lower()
    url_lower = url.lower()

    if GABLEC_URL_PATTERN.search(url_lower) and not ALACARTE_CONTENT_PATTERN.search(url_lower):
        return True

    if _price_count(text) == 0:
        return False

    gablec_hit = bool(GABLEC_CONTENT_PATTERN.search(sample))
    alacarte_hit = bool(ALACARTE_CONTENT_PATTERN.search(sample))
    weekday_sections = sum(
        1 for day in NEXT_DAY_MARKERS[:7]
        if re.search(rf"(^|\n)\s*{day}\b", sample)
    )
    parsed = len(parse_structured_menu(text))

    if gablec_hit and not alacarte_hit and parsed <= 12:
        return True

    if weekday_sections >= 2 and not alacarte_hit and parsed <= 15:
        return True

    return False


def _is_gablec_url(url: str) -> bool:
    return bool(GABLEC_URL_PATTERN.search(url))


def _eligible_regular_candidates(
    candidates: list[tuple[str, str, int]],
) -> list[tuple[str, str, int]]:
    return [
        (text, url, prices)
        for text, url, prices in candidates
        if _price_count(text) > 0 and not is_gablec_menu(text, url)
    ]


def _flush_menu_buffer(buffer: list[str]) -> tuple[str, str] | None:
    while buffer and SECTION_HEADER_PATTERN.match(buffer[0]) and len(buffer) > 1:
        buffer.pop(0)
    if not buffer:
        return None
    name = buffer[0]
    description = " ".join(buffer[1:]).strip() if len(buffer) > 1 else ""
    if len(name) < 2:
        return None
    return name, description


def parse_structured_menu(text: str) -> list[dict]:
    """
    Parse menus where each item is a name (and optional description lines) followed by a price line.
    Works well for Croatian restaurant sites like mrvicadomacakuhinja.hr.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    skip_line_pattern = re.compile(
        r"^(idi na|skip to|početna|kontakt|gal(erija)?|pića|hr\s|en\s|pratite nas|brzi link|©|powered by|alergen|radno vrijeme|facebook|instagram|elementor|\(function)",
        re.I,
    )

    items: list[dict] = []
    buffer: list[str] = []

    for line in lines:
        if skip_line_pattern.search(line) or NAV_LINE_PATTERN.match(line) or len(line) < 3:
            continue
        if line.isupper() and len(line) < 25 and not re.search(r"[\d€]", line):
            continue

        inline_match = INLINE_PRICE_PATTERN.match(line)
        if inline_match:
            items.append({
                "item_name": inline_match.group(1).strip(),
                "price": normalize_price(inline_match.group(2) + " " + (inline_match.group(3) or "€")),
                "description": "",
                "category": "Other",
            })
            buffer = []
            continue

        if line.startswith("("):
            if items:
                items[-1]["description"] = f"{items[-1]['description']} {line}".strip()
            continue

        if PRICE_ONLY_PATTERN.match(line):
            flushed = _flush_menu_buffer(buffer)
            if flushed:
                name, description = flushed
                items.append({
                    "item_name": name,
                    "price": normalize_price(line),
                    "description": description,
                    "category": "Other",
                })
            buffer = []
            continue

        buffer.append(line)

    return items


def extract_regular_menu(name: str, markdown: str) -> dict | None:
    """Prefer deterministic full-menu parsing; fall back to Gemini when needed."""
    parsed_items = parse_structured_menu(markdown)
    price_hits = _price_count(markdown)

    if len(parsed_items) >= 2:
        priced_items = [item for item in parsed_items if item.get("price")]
        if not priced_items:
            return None
        print(f"   Parsed {len(priced_items)} menu items locally.")
        return {
            "restaurant_name": name,
            "is_daily_menu_available": True,
            "specials": priced_items,
            "general_vibe": "Full menu extracted from website.",
        }

    if price_hits >= 5 and len(parsed_items) < 2:
        print(f"   Found {price_hits} prices but only {len(parsed_items)} parsed items; using AI extraction.")

    formatted_prompt = REGULAR_MENU_PROMPT.format(scraped_content=markdown[:MAX_MENU_CHARS])
    response_text = _generate_structured_json(formatted_prompt, max_output_tokens=8192)
    data = parse_scout_result(response_text)
    if not data:
        if len(parsed_items) >= 2:
            return {
                "restaurant_name": name,
                "is_daily_menu_available": True,
                "specials": parsed_items,
                "general_vibe": "Full menu extracted from website.",
            }
        return None

    ai_items = data.get("specials", [])
    if len(parsed_items) > len(ai_items):
        data["specials"] = parsed_items

    if data.get("specials"):
        data["is_daily_menu_available"] = True

    return data


def parse_scout_result(raw: str) -> dict | None:
    if not raw or raw.startswith("❌"):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def normalize_menu_result(raw: str, display_name: str | None = None) -> dict | None:
    data = parse_scout_result(raw)
    if not data:
        return None

    if display_name:
        data["restaurant_name"] = display_name

    for dish in data.get("specials", []):
        if dish.get("price"):
            dish["price"] = normalize_price(dish["price"])
        if dish.get("description"):
            dish["description"] = re.sub(r"\s+", " ", dish["description"]).strip()

    return data


def has_valid_menu(
    data: dict | None,
    menu_type: str = "daily",
    source_text: str = "",
    source_url: str = "",
) -> bool:
    if not data:
        return False

    priced = _priced_dishes(data)
    if not priced:
        return False

    if menu_type == "regular":
        if source_text and is_gablec_menu(source_text, source_url):
            return False
        return len(priced) >= 2

    return bool(data.get("is_daily_menu_available") and len(priced) >= 1)


def extract_fakin_daily_markdown(markdown: str, reference: datetime.date) -> str:
    """Keep only today's section when the date is present on the page."""
    current_date = f"{reference.day}.{reference.month}"
    if current_date not in markdown:
        return markdown

    chunk = markdown.split(current_date, 1)[1]
    lower = chunk.lower()
    end_pos = len(chunk)
    for marker in NEXT_DAY_MARKERS:
        pos = lower.find(marker, 10)
        if pos != -1 and pos < end_pos:
            end_pos = pos
    return f"{current_date}{chunk[:end_pos]}"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</?(p|div|li|tr|td|th|h[1-6])[^>]*>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _fetch_static_page_text(target_url: str, timeout: int = 20) -> tuple[str, int]:
    """Fast HTTP fetch for server-rendered menus (deterministic, no JS wait)."""
    req = Request(target_url, headers={"User-Agent": BROWSER_UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception:
        if target_url.startswith("http://"):
            return _fetch_static_page_text("https://" + target_url[7:], timeout)
        return "", 0

    text = _html_to_text(html)
    return text, _price_count(text)


def _collect_static_menu_candidates(base_url: str) -> list[tuple[str, str, int]]:
    root = base_url.split("#")[0].rstrip("/")
    urls = [base_url.split("#")[0]] + [root + path for path in REGULAR_MENU_PATHS]
    candidates: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    for target_url in urls:
        if target_url in seen or len(seen) >= MAX_PAGES_TO_TRY or _is_gablec_url(target_url):
            continue
        seen.add(target_url)
        text, prices = _fetch_static_page_text(target_url)
        if text and prices > 0:
            candidates.append((text, target_url, prices))

    return candidates


def scrape_with_playwright(url: str, wait_ms: int = 3000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=BROWSER_UA)
        text, _ = _fetch_page_text(page, url)
        browser.close()
        return text


def _count_prices_in_page(page) -> int:
    return page.evaluate(
        """() => (document.body.innerText.match(/\\d+[.,]\\d{2}\\s*(?:€|EUR|kn|HRK)?/gi) || []).length"""
    )


def _wait_for_menu_content(page, minimum_wait_ms: int = 3000) -> int:
    """Wait until menu prices stop increasing (JS finished rendering)."""
    page.wait_for_timeout(minimum_wait_ms)
    last_count = 0
    stable_rounds = 0

    for round_idx in range(20):
        count = _count_prices_in_page(page)
        if count == last_count:
            stable_rounds += 1
            if stable_rounds >= 3:
                break
        else:
            stable_rounds = 0
        last_count = count
        if count == 0 and round_idx >= 8:
            break
        page.wait_for_timeout(800)

    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        count = _count_prices_in_page(page)
        if count > last_count:
            last_count = count
            page.wait_for_timeout(1500)

    return last_count


def _fetch_page_text(page, target_url: str, retries: int = 3) -> tuple[str, int]:
    """Load a URL and return body text + price count; retry and keep the best result."""
    best_text = ""
    best_prices = 0

    for attempt in range(retries):
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
            price_count = _wait_for_menu_content(page, minimum_wait_ms=2000 + attempt * 1000)
            text = page.evaluate("document.body.innerText") or ""
            prices = max(price_count, _price_count(text))
            if prices > best_prices:
                best_text, best_prices = text, prices
        except Exception:
            page.wait_for_timeout(2000)

    return best_text, best_prices


def _pick_best_menu_candidate(candidates: list[tuple[str, str, int]]) -> tuple[str, str, int] | None:
    """Prefer the page with the most parsed dishes; use raw price count as tiebreaker."""
    candidates = _eligible_regular_candidates(candidates)
    if not candidates:
        return None

    scored = [
        (len(parse_structured_menu(text)), prices, text, url)
        for text, url, prices in candidates
    ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, prices, text, url = scored[0]
    return text, url, prices


def scrape_restaurant_website(base_url: str) -> tuple[str, str]:
    """
    Collect menu pages via HTTP and Playwright, then return the page that parses
    into the most menu items (deterministic selection for unchanged websites).
    """
    parsed = urlparse(base_url)
    if not parsed.scheme:
        base_url = "https://" + base_url.lstrip("/")

    static_candidates = _collect_static_menu_candidates(base_url)
    best_static = _pick_best_menu_candidate(static_candidates)
    skip_browser = (
        best_static is not None
        and len(parse_structured_menu(best_static[0])) >= MIN_PARSED_ITEMS_TO_SKIP_BROWSER
    )

    candidates: list[tuple[str, str, int]] = list(static_candidates)
    home_text = static_candidates[0][0] if static_candidates else ""

    if not skip_browser:
        visited: set[str] = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=BROWSER_UA)
            base_domain = urlparse(base_url).netloc

            def fetch(target_url: str) -> tuple[str, int] | None:
                if target_url in visited or len(visited) >= MAX_PAGES_TO_TRY or _is_gablec_url(target_url):
                    return None
                visited.add(target_url)
                text, prices = _fetch_page_text(page, target_url)
                if text and prices > 0:
                    return text, prices
                return None

            root = base_url.split("#")[0].rstrip("/")
            for path in REGULAR_MENU_PATHS:
                result = fetch(root + path)
                if result:
                    text, prices = result
                    candidates.append((text, root + path, prices))

            home_result = fetch(base_url)
            if home_result:
                text, prices = home_result
                candidates.append((text, base_url, prices))
                home_text = text

            try:
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                links = page.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({ href: a.href, text: (a.innerText || '').trim() }))"""
                )
                for link in links:
                    if len(visited) >= MAX_PAGES_TO_TRY:
                        break
                    href = link.get("href", "")
                    text = link.get("text", "")
                    if not href or href in visited:
                        continue
                    if urlparse(href).netloc and urlparse(href).netloc != base_domain:
                        continue
                    if _is_gablec_url(href) or _is_gablec_url(text):
                        continue
                    if REGULAR_MENU_LINK_PATTERN.search(href) or REGULAR_MENU_LINK_PATTERN.search(text):
                        result = fetch(href)
                        if result:
                            page_text, prices = result
                            candidates.append((page_text, href, prices))
            except Exception:
                pass

            browser.close()

    if not candidates:
        return home_text or "", base_url

    picked = _pick_best_menu_candidate(candidates)
    if not picked:
        return home_text or "", base_url

    best_text, best_url, price_hits = picked
    parsed_count = len(parse_structured_menu(best_text))

    if best_url.rstrip("/") != base_url.split("#")[0].rstrip("/") and price_hits > 0:
        print(f"   Using menu page: {best_url} ({parsed_count} items, {price_hits} prices)")

    return best_text, best_url


def scrape_page(name: str, url: str, menu_type: str = "daily") -> str:
    lowered = name.lower()
    if lowered in {"cassandra", "lobby"}:
        return scrape_with_playwright(url)
    if menu_type == "regular":
        text, _ = scrape_restaurant_website(url)
        return text

    try:
        scrape_result = firecrawl.scrape(url, formats=["markdown"])
        markdown = getattr(scrape_result, "markdown", "") or ""
        if markdown and "Invalid upstream proxy" not in markdown:
            return markdown
    except Exception:
        pass

    return scrape_with_playwright(url)


def scout_restaurant(name: str, url: str, menu_type: str = "daily") -> str:
    """Scrapes the target URL and extracts either a daily or regular menu using Gemini."""
    print(f"\n--- Scouting {menu_type.upper()} menu for: {name} ---")

    try:
        if menu_type == "daily":
            markdown = scrape_page(name, url, menu_type=menu_type)
            if not markdown or len(markdown.strip()) < 80:
                return f"❌ Failed to extract text from {url}"

            today = datetime.date.today()
            current_date = f"{today.day}.{today.month}"
            current_day = datetime.datetime.now().strftime("%A")

            if name.lower() == "fakin":
                markdown = extract_fakin_daily_markdown(markdown, today)

            formatted_prompt = MENU_PROMPT.format(
                current_date=current_date,
                current_day=MAPPING.get(current_day, current_day),
                scraped_content=markdown,
            )
            response_text = _generate_structured_json(formatted_prompt)
            return response_text

        for attempt in range(2):
            if attempt:
                print("   Retrying menu scrape...")

            markdown, menu_url = scrape_restaurant_website(url)
            if not markdown or len(markdown.strip()) < 80:
                if attempt == 0:
                    return f"❌ Failed to extract text from {url}"
                break

            if _price_count(markdown) == 0:
                print("   No prices shown on website, skipping.")
                continue

            if is_gablec_menu(markdown, menu_url):
                print("   Daily/gablec menu only — skipping for nearby search.")
                continue

            menu_data = extract_regular_menu(name, markdown)
            if menu_data and has_valid_menu(
                menu_data,
                menu_type=menu_type,
                source_text=markdown,
                source_url=menu_url,
            ):
                return json.dumps(menu_data, ensure_ascii=False)

        return f"❌ Failed to extract menu from {url}"

    except Exception as e:
        return f"❌ Error scouting {name}: {str(e)}"


def scout_hardcoded_favorites() -> list[dict]:
    results = []
    for restaurant in HARDCODED_RESTAURANTS:
        raw = scout_restaurant(restaurant["name"], restaurant["url"])
        parsed = normalize_menu_result(raw, display_name=restaurant["name"])
        if parsed:
            results.append(parsed)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GastroScout AI Menu Finder")
    parser.add_argument(
        "--gmaps",
        action="store_true",
        help="Use Google Maps nearby discovery instead of hardcoded favorites.",
    )
    args = parser.parse_args()

    if args.gmaps:
        print("\n--- MODE: GOOGLE MAPS NEARBY EXPLORATION ---")
        from restaurant_menu_agent.maps_scout import discover_restaurants_with_menus, get_laptop_location

        try:
            lat, lng, label = get_laptop_location()
            print(f"Location: {label} ({lat}, {lng})")

            results = discover_restaurants_with_menus(
                lat,
                lng,
                scout_fn=scout_restaurant,
                has_menu_fn=has_valid_menu,
                normalize_fn=normalize_menu_result,
                radius=3000,
                target=3,
                menu_type="regular",
            )

            if not results:
                print("No suitable restaurants with scrapeable menus found within 3 km.")
            else:
                for result in results:
                    print(
                        f"\nResult for {result.get('restaurant_name')} "
                        f"(Google rating {result.get('google_rating')}):\n"
                        f"{json.dumps(result, ensure_ascii=False, indent=2)}"
                    )
        except Exception as e:
            print(f"Maps search failed: {e}")
    else:
        print("\n--- MODE: HARDCODED FAVORITES ---")
        for result in scout_hardcoded_favorites():
            print(f"\nResult for {result.get('restaurant_name')}:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
