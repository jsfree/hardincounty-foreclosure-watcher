import os
import re
import json
import hashlib
from io import BytesIO

import requests
from bs4 import BeautifulSoup

import pytesseract
from pdf2image import convert_from_bytes

FORECLOSURES_URL = "https://www.co.hardin.tx.us/page/Foreclosures"
STATE_FILE = "seen.json"

# Discord webhook is stored as a GitHub Secret (safer than hardcoding it)
DISCORD_WEBHOOK_URL = os.getenv("https://discord.com/api/webhooks/1465382065850548418/T2lP3RR-riLBMFJXJHkZX7J4mkQTE3yEWF7miknQBGVu7PfWER-1I3VRTej9KaFy7oq9")

# Address matching: include common variations and abbreviations
TARGETS = [
    "503 country wood circle",
    "503 countrywood circle",
    "503 country wood cir",
    "503 countrywood cir",
    "503 country wood cr",
    "503 country wood",
]

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

def get_pdf_links() -> list:
    html = requests.get(FORECLOSURES_URL, timeout=30).text
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
    seen = load_seen()
    pdf_links = get_pdf_links()

    any_new = False

    for url, title in pdf_links:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        if key in seen:
            continue

        any_new = True
        print("New PDF found, OCR scanning:", url)

        try:
            r = requests.get(url, timeout=60)
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
