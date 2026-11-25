import re
import time
from db.database import get_connection
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.gsmarena.com/"
RAW_DIR = Path("data/raw")   # downloaded pages are saved here
DELAY_SECONDS = 4            # be polite: GSMArena blocks fast scrapers

# The 15 phones we want. Key = the name GSMArena uses in its URLs.
TARGET_PHONES = {
    "samsung_galaxy_s21_5g": "Samsung Galaxy S21 5G",
    "samsung_galaxy_s21_ultra_5g": "Samsung Galaxy S21 Ultra 5G",
    "samsung_galaxy_s22_5g": "Samsung Galaxy S22 5G",
    "samsung_galaxy_s22_ultra_5g": "Samsung Galaxy S22 Ultra 5G",
    "samsung_galaxy_s23": "Samsung Galaxy S23",
    "samsung_galaxy_s23_ultra": "Samsung Galaxy S23 Ultra",
    "samsung_galaxy_s23_fe": "Samsung Galaxy S23 FE",
    "samsung_galaxy_s24": "Samsung Galaxy S24",
    "samsung_galaxy_s24_ultra": "Samsung Galaxy S24 Ultra",
    "samsung_galaxy_s25": "Samsung Galaxy S25",
    "samsung_galaxy_s25_ultra": "Samsung Galaxy S25 Ultra",
    "samsung_galaxy_z_fold6": "Samsung Galaxy Z Fold6",
    "samsung_galaxy_z_flip6": "Samsung Galaxy Z Flip6",
    "samsung_galaxy_a55": "Samsung Galaxy A55",
    "samsung_galaxy_a35": "Samsung Galaxy A35",
}

session = requests.Session()
session.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


# ---------- 1. Downloading ----------

def fetch(url, cache_name):
    """Download a page once and keep a copy in data/raw/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{cache_name}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")

    time.sleep(DELAY_SECONDS)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    if "Turnstile" in resp.text[:2000]:
        raise RuntimeError("GSMArena showed its bot check, try again later")
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def find_phone_urls():
    """Go through Samsung's phone list pages until all 15 phones are found."""
    found = {}
    for page in range(1, 31):
        if page == 1:
            url = BASE + "samsung-phones-9.php"
        else:
            url = BASE + f"samsung-phones-f-9-0-p{page}.php"
        soup = BeautifulSoup(fetch(url, f"listing_p{page}"), "lxml")

        for a in soup.select("div.makers li a"):
            slug = a["href"].rsplit("-", 1)[0]   # "samsung_galaxy_s23-12082.php" -> "samsung_galaxy_s23"
            if slug in TARGET_PHONES and slug not in found:
                found[slug] = urljoin(BASE, a["href"])

        print(f"listing page {page}: found {len(found)}/{len(TARGET_PHONES)}")
        if len(found) == len(TARGET_PHONES):
            break
    return found


# ---------- 2. Reading a spec page ----------

def parse_spec_page(html):
    """Read every row of the spec table on a phone's page."""
    soup = BeautifulSoup(html, "lxml")
    name = soup.select_one("h1.specs-phone-name-title").get_text(strip=True)

    rows = []     # list of (category, spec_name, value)
    by_key = {}   # GSMArena tags important cells, e.g. data-spec="batdescription1"
    for table in soup.select("div#specs-list table"):
        category = table.find("th").get_text(strip=True)
        last_name = category
        for tr in table.find_all("tr"):
            ttl = tr.find("td", class_="ttl")
            nfo = tr.find("td", class_="nfo")
            if not nfo:
                continue
            spec_name = (ttl.get_text(" ", strip=True) if ttl else "") or last_name
            last_name = spec_name
            value = nfo.get_text(" | ", strip=True)
            if not value:
                continue
            rows.append((category, spec_name, value))
            if nfo.get("data-spec"):
                by_key.setdefault(nfo["data-spec"], value)

    return {"name": name, "rows": rows, "by_key": by_key}


def find_number(pattern, text, biggest=False):
    """Pull a number out of text, e.g. '3900 mAh' -> 3900."""
    if not text:
        return None
    numbers = [float(n.replace(",", "")) for n in re.findall(pattern, text)]
    if not numbers:
        return None
    return max(numbers) if biggest else numbers[0]


def summarize(parsed):
    """Turn the spec sheet into the columns of the phones table."""
    k = parsed["by_key"]
    return {
        "release_year":    find_number(r"(20\d\d)", k.get("year")),
        "chipset":         k.get("chipset"),
        "display_size_in": find_number(r"([\d.]+)\s*inches", k.get("displaysize")),
        "refresh_rate_hz": find_number(r"(\d+)\s*Hz", k.get("displaytype"), biggest=True),
        "max_ram_gb":      find_number(r"(\d+)\s*GB RAM", k.get("internalmemory"), biggest=True),
        "main_camera_mp":  find_number(r"([\d.]+)\s*MP", k.get("cam1modules")),
        "battery_mah":     find_number(r"(\d+)\s*mAh", k.get("batdescription1")),
        "weight_g":        find_number(r"([\d.]+)\s*g\b", k.get("weight")),
        "price_text":      k.get("price"),
    }


# ---------- 3. Saving to the database ----------

CURRENCIES = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}

def parse_prices(text):
    """'$ 230.61 / € 302.35 / ₹ 54,999' -> [('USD', 230.61), ('EUR', 302.35), ('INR', 54999.0)]"""
    if not text:
        return []
    prices = []
    for symbol, amount in re.findall(r"([$€£₹])\s*([\d,]+(?:\.\d+)?)", text):
        prices.append((CURRENCIES[symbol], float(amount.replace(",", ""))))
    for amount, code in re.findall(r"([\d,]+(?:\.\d+)?)\s*(EUR|USD|GBP|INR)", text):
        prices.append((code, float(amount.replace(",", ""))))
    return prices


def save_phone(conn, url, parsed):
    s = summarize(parsed)

    # Insert the phone, or update it if it's already there (no duplicates).
    phone_id = conn.execute(
        """
        INSERT INTO phones (name, url, release_year, chipset, display_size_in,
                            refresh_rate_hz, max_ram_gb, main_camera_mp,
                            battery_mah, weight_g, price_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            url = EXCLUDED.url, release_year = EXCLUDED.release_year,
            chipset = EXCLUDED.chipset, display_size_in = EXCLUDED.display_size_in,
            refresh_rate_hz = EXCLUDED.refresh_rate_hz, max_ram_gb = EXCLUDED.max_ram_gb,
            main_camera_mp = EXCLUDED.main_camera_mp, battery_mah = EXCLUDED.battery_mah,
            weight_g = EXCLUDED.weight_g, price_text = EXCLUDED.price_text,
            scraped_at = now()
        RETURNING id
        """,
        (parsed["name"], url, s["release_year"], s["chipset"], s["display_size_in"],
         s["refresh_rate_hz"], s["max_ram_gb"], s["main_camera_mp"],
         s["battery_mah"], s["weight_g"], s["price_text"]),
    ).fetchone()[0]

    # Clear old rows for this phone, then insert fresh ones.
    conn.execute("DELETE FROM specifications WHERE phone_id = %s", (phone_id,))
    conn.execute("DELETE FROM prices WHERE phone_id = %s", (phone_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO specifications (phone_id, category, spec_name, spec_value) VALUES (%s, %s, %s, %s)",
            [(phone_id, cat, name, value) for cat, name, value in parsed["rows"]],
        )
        cur.executemany(
            "INSERT INTO prices (phone_id, currency, amount) VALUES (%s, %s, %s)",
            [(phone_id, currency, amount) for currency, amount in parse_prices(s["price_text"])],
        )


def main():
    urls = find_phone_urls()
    saved = 0
    with get_connection() as conn:
        for slug, name in TARGET_PHONES.items():
            try:
                parsed = parse_spec_page(fetch(urls[slug], slug))
                save_phone(conn, urls[slug], parsed)
                conn.commit()
                print(f"[ok]   {parsed['name']:<30} {len(parsed['rows'])} rows")
                saved += 1
            except Exception as e:
                conn.rollback()
                print(f"[FAIL] {name}: {e}")
    print(f"\nSaved {saved}/{len(TARGET_PHONES)} phones")


if __name__ == "__main__":
    main()