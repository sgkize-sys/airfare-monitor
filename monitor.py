import json
import re
import time
import logging
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

CONFIG_FILE = Path(__file__).parent / "config.json"
LOG_FILE = Path(__file__).parent / "prices.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def build_search_url(itinerary: dict) -> str:
    dep = itinerary["departure_airport"]
    arr = itinerary["arrival_airport"]
    out = itinerary["outbound_date"]  # YYYY-MM-DD
    ret = itinerary.get("return_date")

    # Format dates as "Month D, YYYY" for the query string
    out_dt = datetime.strptime(out, "%Y-%m-%d")
    out_str = out_dt.strftime("%B %d, %Y").replace(" 0", " ").replace(",0", ",")

    if ret:
        ret_dt = datetime.strptime(ret, "%Y-%m-%d")
        ret_str = ret_dt.strftime("%B %d, %Y").replace(" 0", " ")
        query = f"flights from {dep} to {arr} on {out_str} returning {ret_str}"
    else:
        query = f"one way flights from {dep} to {arr} on {out_str}"

    encoded = query.replace(" ", "+")
    return f"https://www.google.com/travel/flights?q={encoded}&hl=en&curr=USD"


def dismiss_consent(page):
    for label in ["Accept all", "I agree", "Reject all"]:
        try:
            btn = page.get_by_role("button", name=label)
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(1)
                return
        except Exception:
            pass


def stop_count(label: str) -> int | None:
    """Parse number of stops from a Google Flights aria-label. Returns None if unknown."""
    lower = label.lower()
    if "nonstop" in lower:
        return 0
    m = re.search(r"(\d+)\s+stop", lower)
    if m:
        return int(m.group(1))
    return None


def extract_prices(page, max_connections: int = 1) -> list[int]:
    prices = []
    filtered_prices = []
    found_stop_info = False

    for el in page.query_selector_all("[aria-label]"):
        label = el.get_attribute("aria-label") or ""
        stops = stop_count(label)
        has_price = bool(re.search(r"\$(\d[\d,]*)", label))

        if not has_price:
            continue

        for match in re.finditer(r"\$(\d[\d,]*)", label):
            val = int(match.group(1).replace(",", ""))
            if not (50 < val < 15000):
                continue
            prices.append(val)
            if stops is not None:
                found_stop_info = True
                if stops <= max_connections:
                    filtered_prices.append(val)

    # If we successfully matched stop counts, return filtered list
    if found_stop_info:
        return filtered_prices

    # Fallback: no stop info found — return all prices without filtering
    if prices:
        return prices

    # Last resort: scan raw body text
    for match in re.finditer(r"\$(\d[\d,]+)", page.inner_text("body")):
        val = int(match.group(1).replace(",", ""))
        if 50 < val < 15000:
            prices.append(val)

    return prices


def scrape_price(itinerary: dict) -> float | None:
    name = itinerary["name"]
    url = build_search_url(itinerary)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        try:
            log.debug(f"[{name}] Fetching: {url}")
            page.goto(url, timeout=30000)
            page.wait_for_load_state("domcontentloaded")

            dismiss_consent(page)

            # Wait for flight prices to appear
            try:
                page.wait_for_selector(
                    '[aria-label*="dollar"], [aria-label*="round trip"], [aria-label*="one way"]',
                    timeout=20000,
                )
            except PlaywrightTimeout:
                # Fall back — wait for any dollar sign in visible text
                page.wait_for_function(
                    "document.body.innerText.includes('$')",
                    timeout=15000,
                )

            time.sleep(2)  # let dynamic content settle

            max_conn = itinerary.get("max_connections", 1)
            prices = extract_prices(page, max_connections=max_conn)
            if not prices:
                log.warning(f"[{name}] No prices found — Google may have blocked the request")
                return None

            return float(min(prices))

        except PlaywrightTimeout:
            log.error(f"[{name}] Timed out waiting for results")
            return None
        except Exception as e:
            log.error(f"[{name}] Error: {e}")
            return None
        finally:
            browser.close()


def check_all(config: dict):
    for itinerary in config["itineraries"]:
        name = itinerary["name"]
        threshold = itinerary.get("alert_threshold")
        price = scrape_price(itinerary)

        if price is None:
            log.info(f"[{name}] Price unavailable this check")
            continue

        msg = f"[{name}] ${price:.0f}"
        if threshold and price <= threshold:
            msg += f"  *** BELOW THRESHOLD (${threshold}) ***"
        log.info(msg)


def main():
    config = load_config()
    interval_hours = config.get("check_interval_hours", 6)

    log.info(f"Airfare monitor started — checking every {interval_hours}h")
    log.info(f"Watching {len(config['itineraries'])} itinerary/itineraries")
    log.info(f"Logging to: {LOG_FILE}")

    while True:
        log.info("--- Checking prices ---")
        check_all(config)
        log.info(f"Next check in {interval_hours}h. Press Ctrl+C to stop.")
        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Monitor stopped.")
