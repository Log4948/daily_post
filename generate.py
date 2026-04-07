"""
Dark Side of Days — daily content pipeline
Generates history facts via Claude API, renders to image, ready to post.

Usage:
    python generate.py                  # uses today's date
    python generate.py --date "April 7" # specific date
    python generate.py --preview        # saves image locally, skips posting
"""

import anthropic
import json
import argparse
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import textwrap
import sys
import os

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

PROMPT_TEMPLATE = """You are the writer for a dark history short-form video channel called "Dark Side of Days."

TODAY'S DATE: {date}

CRITICAL RULE — DATE ACCURACY:
Every single fact MUST have occurred on {date} specifically (same month AND same day, any year).
Before writing each fact, verify: "Did this event happen on {date}? Yes/No." If No — discard it.
Do not include events from nearby dates. Do not include events that merely relate to something that started on a different date.

SOURCING:
Draw only from well-documented historical events verifiable in encyclopedias, government archives, or major historical records.
If you are not certain an event occurred on this exact date, omit it.

CONTENT RULES:
- Facts 1–3: well-known events reframed through their darkest verified detail
- Fact 4: obscure, almost unknown — prefix title with "DECLASSIFIED:"
- Voice: cold, sparse, factual. No adjectives unless damning.
- Each body: max 2 sentences, under 35 words
- One fact must include a specific number or statistic
- Never moralize. Never editorialize.

Return ONLY valid JSON, no markdown, no preamble:

{{
  "date": "{date}",
  "facts": [
    {{
      "id": 1,
      "event_date": "April 7, 1945",
      "title": "Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }},
    {{
      "id": 2,
      "event_date": "April 7, 1994",
      "title": "Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }},
    {{
      "id": 3,
      "event_date": "April 7, 1917",
      "title": "Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }},
    {{
      "id": 4,
      "event_date": "April 7, 1953",
      "title": "DECLASSIFIED: Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }}
  ]
}}"""


# ── Step 1: Generate + Validate ───────────────────────────────────────────────

def validate_facts(facts: list, expected_month: int, expected_day: int) -> list:
    """Remove any fact whose event_date doesn't match the expected month/day."""
    valid = []
    for f in facts:
        event_date_str = f.get("event_date", "")
        try:
            # Try parsing "April 7, 1945" style
            parsed = datetime.strptime(event_date_str, "%B %d, %Y")
            if parsed.month == expected_month and parsed.day == expected_day:
                valid.append(f)
            else:
                print(f"    REJECTED fact {f['id']}: '{event_date_str}' — wrong date (got {parsed.month}/{parsed.day}, expected {expected_month}/{expected_day})")
        except ValueError:
            print(f"    REJECTED fact {f['id']}: unparseable date '{event_date_str}'")
    return valid


def generate_facts(date_str: str, max_retries: int = 3) -> dict:
    """Call Claude API, validate dates, retry until 4 valid facts obtained."""
    client = anthropic.Anthropic()
    prompt = PROMPT_TEMPLATE.format(date=date_str)

    expected = datetime.strptime(date_str, "%B %d")
    expected_month = expected.month
    expected_day = expected.day

    all_valid_facts = []

    for attempt in range(1, max_retries + 1):
        print(f"[1/3] Generating facts for {date_str} (attempt {attempt}/{max_retries})...")

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
        facts = data.get("facts", [])

        valid = validate_facts(facts, expected_month, expected_day)
        print(f"    {len(valid)}/{len(facts)} facts passed date validation.")

        # Merge valid facts, avoid duplicates by title
        existing_titles = {f["title"] for f in all_valid_facts}
        for f in valid:
            if f["title"] not in existing_titles:
                all_valid_facts.append(f)
                existing_titles.add(f["title"])

        if len(all_valid_facts) >= 4:
            break

        if attempt < max_retries:
            print(f"    Only {len(all_valid_facts)} valid facts so far, retrying...")

    if len(all_valid_facts) < 4:
        raise RuntimeError(
            f"Only found {len(all_valid_facts)} verified facts for {date_str} after {max_retries} attempts. "
            f"Not enough to render. Try running again."
        )

    # Re-number and take first 4
    final_facts = all_valid_facts[:4]
    for i, f in enumerate(final_facts, 1):
        f["id"] = i

    data["facts"] = final_facts
    print(f"    Final: {len(final_facts)} verified facts.")
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
    """Calculate exact height a fact block will need."""
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

    h  = block_pad_y          # top padding
    h += 40                    # record label
    h += len(title_lines) * 52 # title lines
    h += 12                    # gap
    h += len(body_lines) * 46  # body lines
    h += 50                    # redacted bar
    h += block_pad_y           # bottom padding
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

    # ── Header ────────────────────────────────────────────────────────────────
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

    # ── Date ──────────────────────────────────────────────────────────────────
    draw.text((W // 2, 235), "INCIDENT DATE", font=f_label, fill=hex_to_rgb(TEXT_DIM), anchor="mm")
    draw.text((W // 2, 308), data["date"].upper(), font=f_date, fill=hex_to_rgb(TEXT_PRI), anchor="mm")
    draw.line([(pad, 360), (W - pad, 360)], fill=hex_to_rgb(BORDER), width=2)

    # ── Facts ─────────────────────────────────────────────────────────────────
    block_pad_x = 24
    block_pad_y = 22
    border_w    = 6
    gap         = 18
    fonts       = (f_tiny, f_body, f_title)

    # Pre-measure all blocks to check they fit
    total_facts_h = sum(
        measure_block_height(f, fonts, inner_w, block_pad_x, block_pad_y, border_w) + gap
        for f in data["facts"]
    )
    available = H - 360 - 140  # header area + footer area
    if total_facts_h > available:
        # Scale fonts down if needed
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

        # Record label + event date on same line
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

        # Title
        cy = draw_wrapped_text(draw, fact["title"].upper(), cx, cy,
                               inner_w - border_w - block_pad_x * 2,
                               f_title, hex_to_rgb(TEXT_PRI), line_height=52)
        cy += 12

        # Body
        cy = draw_wrapped_text(draw, fact["body"], cx, cy,
                               inner_w - border_w - block_pad_x * 2,
                               f_body, hex_to_rgb(TEXT_SEC), line_height=46)

        # Redacted bar
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

    # ── Footer ────────────────────────────────────────────────────────────────
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

    # Cross-platform date formatting (no %-d)
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