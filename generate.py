"""
Dark Side of Days — daily content pipeline
Generates history facts via Claude API, renders to image, ready to post.

Usage:
    python generate.py                  # uses today's date
    python generate.py --date "April 6" # specific date
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

# Card dimensions (9:16 portrait — ideal for Shorts/Reels)
W, H = 1080, 1920

# Colors
BG          = "#0e0e0e"
SURFACE     = "#161616"
BORDER      = "#222222"
RED         = "#8b1c1c"
RED_DIM     = "#3d0d0d"
TEXT_PRI    = "#e0dcd2"
TEXT_SEC    = "#888888"
TEXT_DIM    = "#444444"
TEXT_REDACT = "#1e1e1e"   # invisible on dark bg — simulates redaction

PROMPT_TEMPLATE = """You are the writer for a dark history short-form video channel called "Dark Side of Days."

Your job: given a date, produce exactly 4 historical facts formatted as JSON.

Rules:
- Facts 1–3 must be well-known events most people have heard of, reframed through their darkest or most unsettling true detail. Not the headline — the detail that gets buried.
- Fact 4 is the WILDCARD: an obscure event almost no one knows, that sounds unbelievable but is 100% verifiable. Prefix the title with "DECLASSIFIED:".
- Voice: cold, sparse, factual. No adjectives unless they're damning. Write like a declassified document, not a history textbook.
- Each body is max 2 sentences. Under 35 words. Every word earns its place.
- One fact per entry must include a number or statistic — stated without comment.
- Never moralize. Never editorialize. Let the facts speak.
- All facts must be historically accurate. If uncertain, omit.
- Check that the facts happened on the current month and day
- Check that the month and date of current date is when the event happened

Return ONLY valid JSON, no markdown, no preamble:

{{
  "date": "{date}",
  "facts": [
    {{
      "id": 1,
      "title": "Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }},
    {{
      "id": 2,
      "title": "Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }},
    {{
      "id": 3,
      "title": "Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }},
    {{
      "id": 4,
      "title": "DECLASSIFIED: Short punchy title",
      "body": "Setup sentence. The detail they never teach you.",
      "redact": "The specific stat or detail to hide behind redaction."
    }}
  ]
}}

Date to use: {date}"""


# ── Step 1: Generate content ───────────────────────────────────────────────────

def generate_facts(date_str: str) -> dict:
    """Call Claude API and return parsed JSON facts."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    prompt = PROMPT_TEMPLATE.format(date=date_str)

    print(f"[1/3] Generating facts for {date_str}...")
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    print(f"    Got {len(data['facts'])} facts.")
    return data


# ── Step 2: Render image ───────────────────────────────────────────────────────

def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load a system monospace font, fall back to default."""
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
    """Draw text with word wrapping. Returns y position after last line."""
    # Estimate chars per line from font size
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


def render_card(data: dict, output_path: Path):
    """Render the classified document card as a 1080x1920 PNG."""
    print("[2/3] Rendering image...")

    img = Image.new("RGB", (W, H), hex_to_rgb(BG))
    draw = ImageDraw.Draw(img)

    pad = 72   # horizontal padding
    inner_w = W - pad * 2

    # ── Fonts
    f_tiny   = load_font(28)
    f_small  = load_font(32)
    f_body   = load_font(36)
    f_title  = load_font(44, bold=True)
    f_date   = load_font(80, bold=True)
    f_label  = load_font(24)

    # ── Header bar ────────────────────────────────────────────────────────────
    draw_rect(draw, pad, 80, W - pad, 200, fill=hex_to_rgb(SURFACE), outline=hex_to_rgb(BORDER))

    draw.text((pad + 28, 108), "TOP SECRET", font=f_small, fill=hex_to_rgb(RED))
    draw.text((pad + 28, 148), "HISTORICAL RECORD", font=f_tiny, fill=hex_to_rgb(TEXT_DIM))

    doc_lines = [f"FILE: DSH-{data['date'].replace(' ', '').upper()[:6]}",
                 "EYES ONLY", "PAGE: 01 OF 01"]
    dy = 100
    for line in doc_lines:
        bbox = draw.textbbox((0, 0), line, font=f_tiny)
        tw = bbox[2] - bbox[0]
        draw.text((W - pad - 28 - tw, dy), line, font=f_tiny, fill=hex_to_rgb(TEXT_DIM))
        dy += 36

    # ── Date ──────────────────────────────────────────────────────────────────
    draw.text((W // 2, 260), "INCIDENT DATE", font=f_label, fill=hex_to_rgb(TEXT_DIM),
              anchor="mm")
    draw.text((W // 2, 340), data["date"].upper(), font=f_date, fill=hex_to_rgb(TEXT_PRI),
              anchor="mm")

    # Divider
    draw.line([(pad, 400), (W - pad, 400)], fill=hex_to_rgb(BORDER), width=2)

    # ── Facts ─────────────────────────────────────────────────────────────────
    y = 430
    block_pad_x = 28
    block_pad_y = 28
    border_w = 6

    for fact in data["facts"]:
        is_wild = fact["id"] == 4
        accent = RED if is_wild else BORDER
        surface = RED_DIM if is_wild else SURFACE

        # Estimate block height
        body_preview = fact["body"] + " " + fact["redact"]
        approx_lines = max(3, len(textwrap.wrap(body_preview, width=38)))
        block_h = block_pad_y * 2 + 40 + 12 + (approx_lines * 44) + 16

        # Block background
        draw_rect(draw, pad, y, W - pad, y + block_h,
                  fill=hex_to_rgb(surface), outline=hex_to_rgb(accent), width=2, radius=10)

        # Left accent bar
        draw_rect(draw, pad, y, pad + border_w, y + block_h,
                  fill=hex_to_rgb(accent), radius=10)

        cx = pad + border_w + block_pad_x
        cy = y + block_pad_y

        # Record label
        label = f"RECORD 00{fact['id']} {'— DECLASSIFIED' if is_wild else ''}"
        draw.text((cx, cy), label, font=f_tiny,
                  fill=hex_to_rgb(RED if is_wild else TEXT_DIM))
        cy += 40

        # Title
        cy = draw_wrapped_text(draw, fact["title"].upper(), cx, cy,
                               inner_w - border_w - block_pad_x * 2,
                               f_title, hex_to_rgb(TEXT_PRI), line_height=52)
        cy += 12

        # Body text (non-redacted part)
        cy = draw_wrapped_text(draw, fact["body"], cx, cy,
                               inner_w - border_w - block_pad_x * 2,
                               f_body, hex_to_rgb(TEXT_SEC), line_height=46)

        # Redacted block — dark rect simulating censorship bar
        redact_text = fact["redact"]
        try:
            rw = int(f_body.getlength(redact_text[:30]))
        except AttributeError:
            rw = len(redact_text[:30]) * 22
        rw = min(rw + 20, inner_w - border_w - block_pad_x * 2)

        draw_rect(draw, cx, cy + 4, cx + rw, cy + 46,
                  fill=hex_to_rgb("#1a1a1a"), radius=4)
        draw.text((cx + 8, cy + 10), "█ " + redact_text[:28] + "...",
                  font=f_body, fill=hex_to_rgb(TEXT_REDACT))

        cy += 50
        y = y + block_h + 24

    # ── Footer ────────────────────────────────────────────────────────────────
    draw.line([(pad, H - 160), (W - pad, H - 160)], fill=hex_to_rgb(BORDER), width=1)
    draw.text((pad, H - 130), "@darkside.of.days", font=f_small, fill=hex_to_rgb(TEXT_DIM))

    cta = "FOLLOW FOR DAILY FILES"
    bbox = draw.textbbox((0, 0), cta, font=f_small)
    tw = bbox[2] - bbox[0]
    draw.text((W - pad - tw, H - 130), cta, font=f_small, fill=hex_to_rgb(RED))

    img.save(output_path, "PNG", quality=95)
    print(f"    Saved → {output_path}")


# ── Step 3: Post (stubs — wire up your platform APIs here) ────────────────────

def post_to_youtube(image_path: Path, caption: str):
    """
    Upload as a YouTube Community post (image + text).
    Requires: google-auth, google-api-python-client
    Docs: https://developers.google.com/youtube/v3/docs/communityPosts
    Replace this stub with real OAuth + API call.
    """
    print(f"[POST] YouTube stub — would post {image_path} with caption:\n{caption}")


def post_to_instagram(image_path: Path, caption: str):
    """
    Post via Instagram Graph API (requires Facebook Business account).
    Docs: https://developers.facebook.com/docs/instagram-api/guides/content-publishing
    Replace this stub with real token + API call.
    """
    print(f"[POST] Instagram stub — would post {image_path} with caption:\n{caption}")


def build_caption(data: dict) -> str:
    """Build the text caption for the post."""
    lines = [f"What really happened on {data['date']}? 🔎\n"]
    for f in data["facts"]:
        lines.append(f"▪ {f['title']}")
    lines.append("\nTap to reveal the redacted details. Follow for daily files.")
    lines.append("\n#history #darkhistory #todayinhistory #fyp #facts")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None,
                        help='Date string e.g. "April 6". Defaults to today.')
    parser.add_argument("--preview", action="store_true",
                        help="Render only, skip posting.")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%B %-d")

    # 1. Generate
    data = generate_facts(date_str)

    # 2. Render
    slug = date_str.replace(" ", "_").lower()
    output_path = OUTPUT_DIR / f"dark_side_{slug}.png"
    render_card(data, output_path)

    # 3. Save raw JSON alongside image (useful for logging/debugging)
    json_path = OUTPUT_DIR / f"dark_side_{slug}.json"
    json_path.write_text(json.dumps(data, indent=2))

    if args.preview:
        print(f"\n[PREVIEW MODE] Skipping post. Image at: {output_path}")
        return

    # 4. Post
    print("[3/3] Posting...")
    caption = build_caption(data)
    post_to_youtube(output_path, caption)
    post_to_instagram(output_path, caption)
    print("Done.")


if __name__ == "__main__":
    main()
