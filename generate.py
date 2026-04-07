import anthropic
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup
import textwrap

# ── Config ────────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

W, H = 1080, 1920

BG          = "#0e0e0e"
SURFACE     = "#161616"
BORDER      = "#222222"
RED         = "#8b1c1c"
RED_DIM     = "#3d0d0d"
TEXT_PRI    = "#e0dcd2"
TEXT_SEC    = "#888888"
TEXT_DIM    = "#444444"
TEXT_REDACT = "#1e1e1e"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DarkSideOfDays/1.0)"}


# ── Step 1a: Scrape sources ───────────────────────────────────────────────────

def scrape_britannica(month: str, day: int) -> list[dict]:
    """Scrape Britannica's on-this-day page for verified facts."""
    url = f"https://www.britannica.com/on-this-day/{month}-{day}"
    facts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        # Each event block has a year + description
        for item in soup.select(".on-this-day__event, .md-crosslink, [class*='event']"):
            year_el = item.select_one("[class*='year'], strong, b")
            text_el = item.select_one("p, [class*='description'], [class*='text']")
            if year_el and text_el:
                year_text = year_el.get_text(strip=True)
                desc = text_el.get_text(strip=True)
                if year_text.isdigit() and len(desc) > 20:
                    facts.append({
                        "year": int(year_text),
                        "event_date": f"{month} {day}, {year_text}",
                        "body": desc,
                        "source": "Britannica"
                    })
    except Exception as e:
        print(f"    Britannica scrape failed: {e}")
    return facts


def fetch_wikipedia_otd(month: str, day: int) -> list[dict]:
    """Use Wikipedia's free On This Day API — no auth required."""
    # Wikipedia API uses numeric month
    month_num = datetime.strptime(month, "%B").month
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month_num}/{day}"
    facts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        for event in data.get("events", []):
            year = event.get("year", "")
            text = event.get("text", "")
            if year and text and len(text) > 20:
                facts.append({
                    "year": int(year),
                    "event_date": f"{month} {day}, {year}",
                    "body": text,
                    "source": "Wikipedia"
                })
    except Exception as e:
        print(f"    Wikipedia fetch failed: {e}")
    return facts


# ── Step 1b: Claude picks + rewrites ─────────────────────────────────────────

REWRITE_PROMPT = """You are the writer for "Dark Side of Days," a dark history short-form video channel.

I will give you a list of verified historical events that happened on {date}.
Your job: pick exactly 4 and rewrite them in the channel's voice.

SELECTION RULES:
- Pick events with the darkest, most unsettling, or most surprising true angle
- Prefer variety: different eras, different types of events
- Fact 4 must be the most obscure event on the list — prefix its title with "DECLASSIFIED:"
- Do NOT invent any facts. Only use what is provided below.
- Do NOT change dates, names, or numbers from the source material.

WRITING RULES:
- Voice: cold, sparse, factual. Like a declassified document.
- Each body: max 2 sentences, under 35 words
- redact field: a specific number, name, or detail from the event to "hide" behind a redaction bar
- Never moralize. Never editorialize.

SOURCE EVENTS (verified, all happened on {date}):
{events}

Return ONLY valid JSON, no markdown, no preamble:

{{
  "date": "{date}",
  "facts": [
    {{
      "id": 1,
      "event_date": "{date}, YEAR",
      "title": "Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }},
    {{
      "id": 2,
      "event_date": "{date}, YEAR",
      "title": "Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }},
    {{
      "id": 3,
      "event_date": "{date}, YEAR",
      "title": "Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }},
    {{
      "id": 4,
      "event_date": "{date}, YEAR",
      "title": "DECLASSIFIED: Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }}
  ]
}}"""


def generate_facts(date_str: str) -> dict:
    """Scrape verified facts, then use Claude to pick + rewrite 4."""

    # Parse date
    dt = datetime.strptime(date_str, "%B %d")
    month = dt.strftime("%B")
    day = dt.day

    print(f"[1/3] Fetching verified facts for {date_str}...")

    # Scrape both sources
    britannica_facts = scrape_britannica(month, day)
    wikipedia_facts  = fetch_wikipedia_otd(month, day)

    # Merge, deduplicate by year+first 30 chars of body
    seen = set()
    all_facts = []
    for f in britannica_facts + wikipedia_facts:
        key = (f["year"], f["body"][:30])
        if key not in seen:
            seen.add(key)
            all_facts.append(f)

    all_facts.sort(key=lambda x: x["year"])
    print(f"    Found {len(all_facts)} verified source events ({len(britannica_facts)} Britannica, {len(wikipedia_facts)} Wikipedia)")

    if len(all_facts) < 4:
        raise RuntimeError(f"Only found {len(all_facts)} source events for {date_str} — not enough to pick 4.")

    # Format for prompt
    events_text = "\n".join(
        f"[{i+1}] {f['event_date']} ({f['source']}): {f['body']}"
        for i, f in enumerate(all_facts)
    )

    # Claude rewrites only — no hallucination possible
    client = anthropic.Anthropic()
    prompt = REWRITE_PROMPT.format(date=date_str, events=events_text)

    print(f"    Sending {len(all_facts)} source events to Claude for selection + rewrite...")
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    print(f"    Got {len(data['facts'])} rewritten facts.")
    return data