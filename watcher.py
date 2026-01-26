import os
import re
import json
import hashlib
from io import BytesIO
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import requests
from bs4 import BeautifulSoup

import pytesseract
from pdf2image import convert_from_bytes

#FORCE_RESCAN = os.getenv("FORCE_RESCAN", "false").lower() == "true"

FORECLOSURES_URL = "https://www.co.hardin.tx.us/page/Foreclosures"
STATE_FILE = "seen.json"

# Discord webhook is stored as a GitHub Secret (safer than hardcoding it)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Address matching: include common variations and abbreviations
TARGETS = [
    "70 DOGWOOD",
    "503 COUNTRYWOOD CIRCLE",
    "503 COUNTRYWOOD",
    "503 COUNTRY WOOD",
    "503 Country Wood Circle",
    "503 Country Wood",
    "503 Countrywood",
    "503 country wood circle",
    "503 countrywood circle",
    "503 country wood cir",
    "503 countrywood cir",
    "503 country wood cr",
    "503 country wood",
]
def build_session() -> requests.Session:
    session = requests.Session()

        # Retries for slow servers
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    # Some servers act better with a normal UA
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; foreclosure-watcher/1.0; +https://github.com/)"
    })
    
    return session

def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def load_seen() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_seen(seen: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)

def notify_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL not set; skipping Discord notification.")
        return

    payload = {"content": message}
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()

def extract_text_with_ocr(pdf_bytes: bytes) -> str:
    # Convert each PDF page to an image (OCR works on images)
    images = convert_from_bytes(pdf_bytes, dpi=300)

    text_parts = []
    for img in images:
        # OCR each page image into text
        txt = pytesseract.image_to_string(img)
        text_parts.append(txt)

    return "\n".join(text_parts)

def get_pdf_links(session: requests.Session) -> list:
    resp = session.get(FORECLOSURES_URL, timeout=(15, 90))
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            url = href if href.startswith("http") else "https://www.co.hardin.tx.us" + href
            title = normalize(a.get_text(" ", strip=True))
            links.append((url, title))

    # Deduplicate while preserving order
    deduped = []
    seen_urls = set()
    for url, title in links:
        if url not in seen_urls:
            seen_urls.add(url)
            deduped.append((url, title))

    return deduped

def main():
    notify_discord("Foreclosure watcher ran successfully (test ping).")
    session = build_session()
    seen = load_seen()
    try:
        pdf_links = get_pdf_links(session)
    except Exception as e:
        print("Could not load foreclosures page. Will retry next run. Error:", str(e))
        return

    any_new = False

    for url, title in pdf_links:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        if (not FORCE_RESCAN) and (key in seen):
            continue

        any_new = True
        print("New PDF found, OCR scanning:", url)

        try:
            r = requests.get(url, timeout=(15, 180))
            r.raise_for_status()

            raw_text = extract_text_with_ocr(r.content)
            text = normalize(raw_text)

            hit = any(target in text for target in TARGETS)

            seen[key] = {"url": url, "title": title, "hit": hit}

            if hit:
                msg = (
                    "🚨 **Hardin County Foreclosure Notice Match**\n"
                    "**Address:** 503 Country Wood Circle\n"
                    f"**PDF Title:** {title}\n"
                    f"**PDF Link:** {url}"
                )
                notify_discord(msg)

        except Exception as e:
            seen[key] = {"url": url, "title": title, "status": f"error: {str(e)}"}

    save_seen(seen)

    if not any_new:
        print("No new PDFs since last run.")
    else:
        print("Run complete. Updated seen.json.")

if __name__ == "__main__":
    main()
