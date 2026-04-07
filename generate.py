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

SELECTION RULES — pick one fact per category:
- SLOT 1 — DARK: The most disturbing or sinister event. Deaths, crimes, atrocities, failures.
- SLOT 2 — STRANGE/IRONIC: Something bizarre, absurd, or deeply ironic. Weird twists of fate, unintended consequences, things that sound made up but aren't.
- SLOT 3 — SHOCKING STATISTIC: An event where the scale or number is the gut punch. Pick the fact with the most jaw-dropping figure.
- SLOT 4 — DECLASSIFIED (most obscure): The event almost no one knows about. Must be the least well-known on the list. Prefix title with "DECLASSIFIED:"

WRITING RULES:
- Voice: cold, sparse, factual. Like a declassified document.
- Each body: max 2 sentences, under 35 words
- Let the category drive the angle — slot 2 can have a dry, ironic edge. Slot 3 should lead with the number.
- redact field: the single most surprising detail, number, or name from the event
- Do NOT invent any facts. Only use what is provided below.
- Do NOT change dates, names, or numbers from the source material.
- Never moralize. Never editorialize.

SOURCE EVENTS (verified, all happened on {date}):
{events}

Return ONLY valid JSON, no markdown, no preamble:

{{
  "date": "{date}",
  "facts": [
    {{
      "id": 1,
      "slot": "DARK",
      "event_date": "{date}, YEAR",
      "title": "Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }},
    {{
      "id": 2,
      "slot": "STRANGE",
      "event_date": "{date}, YEAR",
      "title": "Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }},
    {{
      "id": 3,
      "slot": "STAT",
      "event_date": "{date}, YEAR",
      "title": "Short punchy title",
      "body": "One or two sentences max.",
      "redact": "Specific detail to redact."
    }},
    {{
      "id": 4,
      "slot": "DECLASSIFIED",
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

# ── Step 2: Render image ───────────────────────────────────────────────────────

def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/System/Library/Fonts/Courier New Bold.ttf" if bold else
        "/System/Library/Fonts/Courier New.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_wrapped_text(draw, text, x, y, max_width, font, fill, line_height=None):
    try:
        char_w = font.getlength("M")
    except AttributeError:
        char_w = font.size * 0.6
    chars_per_line = max(1, int(max_width / char_w))
    lines = textwrap.wrap(text, width=chars_per_line)
    lh = line_height or (font.size + 8)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += lh
    return y


def draw_rect(draw, x1, y1, x2, y2, fill=None, outline=None, width=1, radius=8):
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill, outline=outline, width=width)


def measure_block_height(fact, fonts, inner_w, block_pad_x, block_pad_y, border_w):
    f_tiny, f_body, f_title = fonts
    try:
        char_w_title = f_title.getlength("M")
        char_w_body  = f_body.getlength("M")
    except AttributeError:
        char_w_title = f_title.size * 0.6
        char_w_body  = f_body.size * 0.6

    text_w = inner_w - border_w - block_pad_x * 2

    title_lines = textwrap.wrap(fact["title"].upper(), width=max(1, int(text_w / char_w_title)))
    body_lines  = textwrap.wrap(fact["body"],          width=max(1, int(text_w / char_w_body)))

    h  = block_pad_y
    h += 40
    h += len(title_lines) * 52
    h += 12
    h += len(body_lines) * 46
    h += 50
    h += block_pad_y
    return h


def render_card(data: dict, output_path: Path):
    print("[2/3] Rendering image...")

    img = Image.new("RGB", (W, H), hex_to_rgb(BG))
    draw = ImageDraw.Draw(img)

    pad = 56
    inner_w = W - pad * 2

    f_tiny   = load_font(26)
    f_small  = load_font(30)
    f_body   = load_font(32)
    f_title  = load_font(38, bold=True)
    f_date   = load_font(72, bold=True)
    f_label  = load_font(22)
    f_event  = load_font(24)

    # ── Header
    draw_rect(draw, pad, 70, W - pad, 185, fill=hex_to_rgb(SURFACE), outline=hex_to_rgb(BORDER))
    draw.text((pad + 24, 95),  "TOP SECRET",        font=f_small, fill=hex_to_rgb(RED))
    draw.text((pad + 24, 133), "HISTORICAL RECORD", font=f_tiny,  fill=hex_to_rgb(TEXT_DIM))

    doc_lines = [f"FILE: DSH-{data['date'].replace(' ', '').upper()[:6]}",
                 "EYES ONLY", "PAGE: 01 OF 01"]
    dy = 88
    for line in doc_lines:
        bbox = draw.textbbox((0, 0), line, font=f_tiny)
        tw = bbox[2] - bbox[0]
        draw.text((W - pad - 24 - tw, dy), line, font=f_tiny, fill=hex_to_rgb(TEXT_DIM))
        dy += 34

    # ── Date
    draw.text((W // 2, 235), "INCIDENT DATE", font=f_label, fill=hex_to_rgb(TEXT_DIM), anchor="mm")
    draw.text((W // 2, 308), data["date"].upper(), font=f_date, fill=hex_to_rgb(TEXT_PRI), anchor="mm")
    draw.line([(pad, 360), (W - pad, 360)], fill=hex_to_rgb(BORDER), width=2)

    # ── Facts
    block_pad_x = 24
    block_pad_y = 22
    border_w    = 6
    gap         = 18
    fonts       = (f_tiny, f_body, f_title)

    total_facts_h = sum(
        measure_block_height(f, fonts, inner_w, block_pad_x, block_pad_y, border_w) + gap
        for f in data["facts"]
    )
    available = H - 360 - 140
    if total_facts_h > available:
        scale = available / total_facts_h
        new_body  = max(24, int(32 * scale))
        new_title = max(28, int(38 * scale))
        f_body  = load_font(new_body)
        f_title = load_font(new_title, bold=True)
        fonts   = (f_tiny, f_body, f_title)
        print(f"    Auto-scaled fonts: body={new_body} title={new_title}")

    y = 378
    for fact in data["facts"]:
        is_wild = fact["id"] == 4
        accent  = RED if is_wild else BORDER
        surface = RED_DIM if is_wild else SURFACE

        block_h = measure_block_height(fact, fonts, inner_w, block_pad_x, block_pad_y, border_w)

        draw_rect(draw, pad, y, W - pad, y + block_h,
                  fill=hex_to_rgb(surface), outline=hex_to_rgb(accent), width=2, radius=10)
        draw_rect(draw, pad, y, pad + border_w, y + block_h,
                  fill=hex_to_rgb(accent), radius=10)

        cx = pad + border_w + block_pad_x
        cy = y + block_pad_y

        label = f"RECORD 00{fact['id']} {'— DECLASSIFIED' if is_wild else ''}"
        draw.text((cx, cy), label, font=f_tiny,
                  fill=hex_to_rgb(RED if is_wild else TEXT_DIM))

        event_date = fact.get("event_date", "")
        if event_date:
            bbox = draw.textbbox((0, 0), event_date, font=f_event)
            ew = bbox[2] - bbox[0]
            draw.text((W - pad - border_w - block_pad_x - ew, cy),
                      event_date, font=f_event, fill=hex_to_rgb(RED if is_wild else TEXT_SEC))
        cy += 40

        cy = draw_wrapped_text(draw, fact["title"].upper(), cx, cy,
                               inner_w - border_w - block_pad_x * 2,
                               f_title, hex_to_rgb(TEXT_PRI), line_height=52)
        cy += 12

        cy = draw_wrapped_text(draw, fact["body"], cx, cy,
                               inner_w - border_w - block_pad_x * 2,
                               f_body, hex_to_rgb(TEXT_SEC), line_height=46)

        redact_text = fact["redact"]
        try:
            rw = int(f_body.getlength(redact_text[:30]))
        except AttributeError:
            rw = len(redact_text[:30]) * 20
        rw = min(rw + 20, inner_w - border_w - block_pad_x * 2)
        draw_rect(draw, cx, cy + 4, cx + rw, cy + 42,
                  fill=hex_to_rgb("#1a1a1a"), radius=4)
        draw.text((cx + 8, cy + 10), "█ " + redact_text[:28] + "...",
                  font=f_body, fill=hex_to_rgb(TEXT_REDACT))

        y += block_h + gap

    # ── Footer
    draw.line([(pad, H - 120), (W - pad, H - 120)], fill=hex_to_rgb(BORDER), width=1)
    draw.text((pad, H - 95), "@darkside.of.days", font=f_small, fill=hex_to_rgb(TEXT_DIM))
    cta = "FOLLOW FOR DAILY FILES"
    bbox = draw.textbbox((0, 0), cta, font=f_small)
    tw = bbox[2] - bbox[0]
    draw.text((W - pad - tw, H - 95), cta, font=f_small, fill=hex_to_rgb(RED))

    img.save(output_path, "PNG", quality=95)
    print(f"    Saved → {output_path}")


# ── Step 3: Post stubs ────────────────────────────────────────────────────────

def post_to_youtube(image_path: Path, caption: str):
    print(f"[POST] YouTube stub — would post {image_path}")

def post_to_instagram(image_path: Path, caption: str):
    print(f"[POST] Instagram stub — would post {image_path}")

def build_caption(data: dict) -> str:
    lines = [f"What really happened on {data['date']}? 🔎\n"]
    for f in data["facts"]:
        lines.append(f"▪ {f['title']}")
    lines.append("\nTap to reveal the redacted details. Follow for daily files.")
    lines.append("\n#history #darkhistory #todayinhistory #fyp #facts")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    if args.date:
        date_str = args.date
    else:
        now = datetime.now()
        date_str = now.strftime("%B ") + str(now.day)

    data = generate_facts(date_str)

    slug = date_str.replace(" ", "_").lower()
    output_path = OUTPUT_DIR / f"dark_side_{slug}.png"
    render_card(data, output_path)

    json_path = OUTPUT_DIR / f"dark_side_{slug}.json"
    json_path.write_text(json.dumps(data, indent=2))

    if args.preview:
        print(f"\n[PREVIEW MODE] Skipping post. Image at: {output_path}")
        return

    print("[3/3] Posting...")
    caption = build_caption(data)
    post_to_youtube(output_path, caption)
    post_to_instagram(output_path, caption)
    print("Done.")


if __name__ == "__main__":
    main()