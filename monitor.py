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

# Matches each flight block in Google Flights page text:
#   departure_time \n arrow_line \n arrival_time \n airline \n duration \n route \n stops \n ... \n $price
FLIGHT_RE = re.compile(
    r'(\d{1,2}:\d{2} ?[AP]M)\n'          # departure time
    r'.+?\n'                               # separator (arrow character)
    r'(\d{1,2}:\d{2} ?[AP]M[^\n]*)\n'    # arrival time + optional +1
    r'([^\n]+)\n'                          # airline(s)
    r'[^\n]+\n'                            # duration
    r'[^\n]+\n'                            # route
    r'(\d+ stops?|Nonstop)\n'              # stops
    r'((?:[^\n]+\n){0,6})'                 # optional lines: layovers, CO2, fare class
    r'\$(\d[\d,]*)',                       # price
    re.IGNORECASE,
)


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def build_search_url(itinerary: dict) -> str:
    dep = itinerary["departure_airport"]
    arr = itinerary["arrival_airport"]
    out = itinerary["outbound_date"]
    ret = itinerary.get("return_date")

    out_str = datetime.strptime(out, "%Y-%m-%d").strftime("%B %d, %Y").replace(" 0", " ")

    if ret:
        ret_str = datetime.strptime(ret, "%Y-%m-%d").strftime("%B %d, %Y").replace(" 0", " ")
        query = f"flights from {dep} to {arr} on {out_str} returning {ret_str}"
    else:
        query = f"one way flights from {dep} to {arr} on {out_str}"

    return f"https://www.google.com/travel/flights?q={query.replace(' ', '+')}&hl=en&curr=USD"


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


def parse_flights(body: str, max_connections: int = 1) -> list[dict]:
    flights = []
    for m in FLIGHT_RE.finditer(body):
        dep_time, arr_time, airline, stops_str, middle, price_str = m.groups()

        if "basic economy" in middle.lower():
            continue

        price = int(price_str.replace(",", ""))
        if not (50 < price < 15000):
            continue

        if stops_str.lower() == "nonstop":
            stops = 0
        else:
            stops = int(re.search(r"\d+", stops_str).group())

        if stops > max_connections:
            continue

        flights.append({
            "price": price,
            "airline": airline.strip(),
            "dep_time": dep_time.strip().upper().replace(" ", ""),
            "arr_time": arr_time.strip().upper().replace(" ", ""),
            "stops": stops,
        })
    return flights


def scrape_flights(itinerary: dict) -> list[dict]:
    name = itinerary["name"]
    url = build_search_url(itinerary)
    max_conn = int(itinerary.get("max_connections", 1))

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
            page.goto(url, timeout=30000)
            page.wait_for_load_state("domcontentloaded")
            dismiss_consent(page)

            page.wait_for_function(
                "document.body.innerText.includes('$')",
                timeout=20000,
            )
            time.sleep(2)

            flights = parse_flights(page.inner_text("body"), max_connections=max_conn)
            if not flights:
                log.warning(f"[{name}] No qualifying flights found")
            return sorted(flights, key=lambda f: f["price"])

        except PlaywrightTimeout:
            log.error(f"[{name}] Timed out waiting for results")
            return []
        except Exception as e:
            log.error(f"[{name}] Error: {e}")
            return []
        finally:
            browser.close()


def check_all(config: dict):
    for itinerary in config["itineraries"]:
        name = itinerary["name"]
        threshold = itinerary.get("alert_threshold")
        flights = scrape_flights(itinerary)

        if not flights:
            log.info(f"[{name}] Price unavailable this check")
            continue

        stops_label = {0: "Nonstop", 1: "1 stop", 2: "2 stops", 3: "3 stops"}
        for flight in flights:
            price = flight["price"]
            airline = flight["airline"]
            dep_time = flight["dep_time"]
            arr_time = flight["arr_time"]
            stops = stops_label.get(flight["stops"], f"{flight['stops']} stops")

            msg = f"[{name}] ${price} | {airline} | {dep_time} | {arr_time} | {stops}"
            if threshold and price <= threshold:
                msg += f"  *** BELOW THRESHOLD (${threshold}) ***"
            log.info(msg)


def main():
    config = load_config()
    interval_hours = config.get("check_interval_hours", 6)

    log.info(f"Airfare monitor started -- checking every {interval_hours}h")
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
