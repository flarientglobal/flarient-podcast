#!/usr/bin/env python3
"""
Daily Space Podcast by Flarient — Generator
- Episode types: daily (30min, Mon-Sat), weekly (45min, Sunday), breaking (30min, significant events)
- Fetches content from Flarient API (live data, blog posts, events, fact checks, daily brief, This Day in History)
- Deduplication: tracks covered content IDs in covered_content.json to avoid repeats
- Generates conversational script with Gemini (two hosts, unique hook)
- Synthesizes speech with edge-tts (zero cost, consistent voices: Christopher & Jenny)
- Masters audio with ffmpeg (silence removal, background music bed, chapters, loudnorm to -16 LUFS, ID3 metadata)
- Generates per-episode cover art with Pillow (3000x3000, date + Kp + type)
- Saves full transcript as .txt
- Creates GitHub Release with MP3, cover art, and transcript (permanent public URLs)
- Updates podcast.xml RSS feed and commits it back to the repo
- One episode per day max (checks if release exists before generating)
Total cost: Free
"""

import os, sys, json, re, subprocess, asyncio, datetime, hashlib, math
from pathlib import Path
import requests
import edge_tts

# ── Configuration ──────────────────────────────────────────────────────────
FLARIENT_API = os.environ.get("FLARIENT_API_URL", "https://flarient.com").rstrip("/")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
EPISODE_DATE = datetime.date.today().isoformat()
EPISODE_TAG = f"podcast-{EPISODE_DATE}"
MP3_FILENAME = f"flarient-podcast-{EPISODE_DATE}.mp3"
COVER_FILENAME = f"cover-{EPISODE_DATE}.jpg"
TRANSCRIPT_FILENAME = f"transcript-{EPISODE_DATE}.txt"
WORK_DIR = Path("/tmp/podcast")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))

# Episode type: "daily", "weekly", "breaking", or "auto" (auto-detect from day of week)
EPISODE_TYPE = os.environ.get("EPISODE_TYPE", "auto")
if EPISODE_TYPE == "auto":
    if datetime.date.today().weekday() == 6:  # Sunday
        EPISODE_TYPE = "weekly"
    else:
        EPISODE_TYPE = "daily"

# Voice assignment (Microsoft Azure neural voice, free via edge-tts)
# Ollie is the podcast host persona. The TTS voice is en-GB-RyanNeural because
# en-GB-OllieMultilingualNeural is an Azure-only voice not available through
# the edge-tts (Edge browser) free endpoint. Ryan is a warm British English male voice.
HOST_VOICE = "en-GB-RyanNeural"  # Single host — Ollie (consistent every episode)

# Podcast metadata
PODCAST_TITLE = "Daily Space Podcast by Flarient"
PODCAST_DESC = "Your daily conversation about space weather, solar activity, aurora forecasts, and cosmic events. Host Ollie breaks down the latest data from NOAA, NASA, and ESA into plain English for aurora chasers, ham radio operators, satellite operators, and anyone curious about what the Sun is doing today."
PODCAST_AUTHOR = "Flarient"
PODCAST_CATEGORY = "Science"
PODCAST_COVER = "https://flarientglobal.github.io/flarient-podcast/podcast-cover.png"
PODCAST_LINK = "https://flarient.com/podcast"

# Gemini model fallback chain (free tier) — updated to current models.
# Runtime discovery (below) will override this list with live API data when available,
# making this resilient to future deprecations.
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]


def log(msg):
    print(f"[PODCAST] {msg}", flush=True)


# ── Runtime Gemini model discovery ─────────────────────────────────────────
# Lists available models from the API and picks flash variants (fast + free tier).
# This prevents failures when Google deprecates specific model versions.
def discover_gemini_models(client):
    try:
        log("  Discovering available Gemini models via API...")
        available = []
        for model in client.models.list():
            name = model.name.replace("models/", "")
            name_lower = name.lower()
            # Only keep flash models (fast, free tier)
            if "flash" not in name_lower:
                continue
            # Exclude non-text-generation models (TTS, image, audio, live, embed, vision)
            exclude_kw = ["embed", "vision", "tts", "image", "audio", "live", "native"]
            if any(kw in name_lower for kw in exclude_kw):
                continue
            available.append(name)
        if available:
            # Sort: non-lite first, then by version number (highest first)
            def sort_key(n):
                is_lite = 1 if "lite" in n.lower() else 0
                vm = re.search(r'(\d+\.?\d*)', n)
                ver = float(vm.group(1)) if vm else 0
                return (is_lite, -ver)
            available.sort(key=sort_key)
            log(f"  Found {len(available)} text flash models: {', '.join(available[:5])}")
            return available
    except Exception as e:
        log(f"  Model discovery failed ({e}), using fallback list")
    return None


# ── 0. Check if episode already exists (one per day max) ───────────────────
def release_exists():
    env = os.environ.copy()
    env["GH_TOKEN"] = GH_TOKEN
    result = subprocess.run(["gh", "release", "view", EPISODE_TAG], capture_output=True, text=True, env=env)
    return result.returncode == 0


# ── 1. Fetch content from Flarient API ─────────────────────────────────────
def fetch_content():
    log("Fetching content from Flarient API...")
    resp = requests.get(f"{FLARIENT_API}/api/functions/getPodcastContent", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    log(f"  Events: {len(data.get('events', []))}, Articles: {len(data.get('articles', []))}, "
        f"Fact checks: {len(data.get('fact_checks', []))}, Daily brief: {'yes' if data.get('daily_brief') else 'no'}, "
        f"This Day: {len(data.get('this_day_in_history', []))}")
    return data


# ── 1b. Deduplication: load/save covered content IDs ──────────────────────
def load_covered():
    path = REPO_DIR / "covered_content.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {"covered_ids": [], "last_updated": None}


def save_covered(covered, new_ids):
    covered["covered_ids"] = list(set(covered.get("covered_ids", []) + new_ids))[-200:]  # Keep last 200
    covered["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = REPO_DIR / "covered_content.json"
    path.write_text(json.dumps(covered, indent=2))
    log(f"  covered_content.json updated ({len(covered['covered_ids'])} total IDs)")


def filter_covered(content, covered):
    covered_ids = set(covered.get("covered_ids", []))
    new_ids = []
    for key in ["events", "articles", "fact_checks"]:
        original = content.get(key, [])
        filtered = [item for item in original if item.get("id") not in covered_ids]
        new_ids.extend([item.get("id") for item in filtered if item.get("id")])
        content[key] = filtered
        if len(original) != len(filtered):
            log(f"  Dedup: {key} {len(original)} -> {len(filtered)} (removed {len(original) - len(filtered)} already covered)")
    content["_new_covered_ids"] = new_ids
    return content


# ── 2. Generate conversational script with Gemini ──────────────────────────
def build_prompt(content, episode_type):
    live = content.get("live_data") or {}
    events = content.get("events") or []
    articles = content.get("articles") or []
    fact_checks = content.get("fact_checks") or []
    daily_brief = content.get("daily_brief") or {}
    this_day = content.get("this_day_in_history") or []

    # Build live data summary
    live_summary = ""
    if live:
        kp = live.get("kp_index") or live.get("kp") or "unknown"
        bz = live.get("bz") or "unknown"
        speed = live.get("solar_wind_speed") or live.get("speed") or "unknown"
        flare = live.get("flare_class") or live.get("xray_class") or "unknown"
        dst = live.get("dst") or "unknown"
        live_summary = f"""CURRENT SPACE WEATHER DATA (live from NOAA/SWPC):
- Kp index: {kp}
- Bz (magnetic field): {bz} nT
- Solar wind speed: {speed} km/s
- X-ray flare class: {flare}
- Dst index: {dst}
- Aurora forecast: {live.get('aurora_forecast', 'see data')}
- Summary: {live.get('summary', live.get('current_summary', 'No summary available'))}
"""

    # Build events summary
    events_summary = ""
    if events:
        events_summary = "\n\nRECENT SPACE EVENTS:\n"
        for e in events[:5]:
            events_summary += f"- {e['title']} (Type: {e['event_type']}, Severity: {e.get('severity', 'unknown')}, Status: {e.get('status', 'unknown')})\n"
            events_summary += f"  Summary: {e['summary']}\n"
            if e.get('geographic_relevance'):
                events_summary += f"  Affected regions: {e['geographic_relevance']}\n"
            events_summary += f"  URL: {e['url']}\n"

    # Build articles summary
    articles_summary = ""
    if articles:
        articles_summary = "\n\nRECENT BLOG ARTICLES:\n"
        for a in articles[:5]:
            articles_summary += f"- Title: {a['title']}\n"
            articles_summary += f"  Category: {a.get('category', 'general')}\n"
            articles_summary += f"  Excerpt: {a.get('excerpt', '')}\n"
            articles_summary += f"  Content excerpt: {a.get('content', '')[:2000]}\n"
            articles_summary += f"  URL: {a['url']}\n"

    # Build fact checks summary
    fact_checks_summary = ""
    if fact_checks:
        fact_checks_summary = "\n\nRECENT FACT CHECKS:\n"
        for f in fact_checks[:5]:
            fact_checks_summary += f"- Claim: {f.get('claim', f.get('title', ''))}\n"
            fact_checks_summary += f"  Verdict: {f.get('verdict', 'unknown')}\n"
            fact_checks_summary += f"  Explanation: {f.get('explanation', '')}\n"
            fact_checks_summary += f"  URL: {f['url']}\n"

    # Build daily brief summary
    brief_summary = ""
    if daily_brief:
        brief_summary = "\n\nDAILY BRIEF / HIGHLIGHT:\n"
        brief_summary += f"- Title: {daily_brief.get('title', '')}\n"
        brief_summary += f"  Subtitle: {daily_brief.get('subtitle', '')}\n"
        brief_summary += f"  Description: {daily_brief.get('description', '')}\n"
        if daily_brief.get('intro'):
            brief_summary += f"  Intro: {daily_brief['intro'][:1500]}\n"
        if daily_brief.get('implications'):
            brief_summary += f"  Implications: {daily_brief['implications'][:1500]}\n"
        if daily_brief.get('outlook'):
            brief_summary += f"  Outlook: {daily_brief['outlook'][:1500]}\n"
        if daily_brief.get('conclusion'):
            brief_summary += f"  Conclusion: {daily_brief['conclusion'][:1500]}\n"

    # Build This Day in History summary
    this_day_summary = ""
    if this_day:
        this_day_summary = "\n\nTHIS DAY IN SPACE WEATHER HISTORY (for cold-open segment):\n"
        for h in this_day[:3]:
            this_day_summary += f"- Year: {h.get('year', '?')}, Title: {h.get('title', '')}, Detail: {h.get('detail', '')}\n"
            if h.get('source_name'):
                this_day_summary += f"  Source: {h['source_name']}\n"

    # Episode type-specific instructions
    type_instructions = ""
    if episode_type == "weekly":
        type_instructions = """
WEEKLY ROUNDUP MODE (45 minutes, ~7,000 words):
This is a SUNDAY WEEKLY ROUNDUP episode. Instead of the daily format, focus on the TOP 5 space weather events of the past week.
Structure:
1. HOOK (30-60s) — tease the #1 story of the week
2. BRIEF INTRO — mention this is the weekly roundup
3. THIS DAY IN HISTORY (60-90s) — short entertaining cold-open fact
4. TOP 5 STORIES OF THE WEEK (30 min) — rank the week's biggest space weather events, discuss each in depth (~5-6 min each)
5. WEEK AHEAD (3-5 min) — what to watch for next week
6. FACT CHECKS (3-5 min) — cover any fact checks from the week
7. CTAs (1-2 min)
Make it feel like a weekly review show, not a daily report.
"""
    elif episode_type == "breaking":
        type_instructions = """
BREAKING EVENT MODE (30 minutes, ~4,500-5,000 words):
A significant space weather event is happening RIGHT NOW (G4+ geomagnetic storm or X-class solar flare).
Lead with the breaking event as the TOP story (first 5-7 minutes), then continue with regular daily content.
Structure:
1. HOOK (30-60s) — lead with the breaking event dramatically
2. BRIEF INTRO — mention this is a breaking special
3. BREAKING EVENT (5-7 min) — detailed coverage of the significant event
4. THIS DAY IN HISTORY (60-90s) — short entertaining cold-open fact
5. SPACE WEATHER REPORT (3-5 min) — current conditions
6. BLOG ARTICLES (3-5 min)
7. SPACE EVENTS (3-5 min)
8. FACT CHECKS (2-3 min)
9. CTAs (1-2 min)
The breaking event takes priority but still cover other content per the normal rules.
"""
    else:
        type_instructions = """
DAILY MODE (30 minutes, ~4,500-5,000 words):
Standard daily episode covering all content areas.
"""

    return f"""You are generating a podcast episode for "Daily Space Podcast by Flarient", a space weather and astronomy podcast.

{type_instructions}

SINGLE HOST (consistent every episode):
- Ollie (male, British accent) — a knowledgeable and engaging space weather expert who explains the science in an accessible, conversational way. Ollie hosts the show alone, speaking directly to the listener as if sharing a fascinating story with a friend.

PODCAST STRUCTURE:

1. HOOK (30-60 seconds, ~100-150 words): A UNIQUE, COMPELLING opening that grabs attention immediately. This must be DIFFERENT every single episode. Do NOT use generic openings like "Welcome back to the podcast" or "Today we're talking about...". Instead, start with:
   - A surprising fact or statistic
   - A dramatic "what if" scenario
   - A vivid description of something happening in space right now
   - A question that makes the listener want to know the answer
   The hook should relate to today's most interesting content but be phrased as a teaser.
   The hook comes FIRST, before anything else. Do not introduce the podcast or the hosts before the hook.

2. CONSISTENT INTRO (15-30 seconds): After the hook, use this EXACT intro every single episode (Ollie says it):
   "You're listening to the Daily Space Podcast by Flarient, your daily conversation about space weather, solar activity, and cosmic events. I'm Ollie, and today is [say the date in natural language like 'Wednesday, August nineteenth']."
   Then Ollie adds: "Let's get into what the Sun is doing today."
   This intro must be word-for-word the same every episode (only the date changes).

3. THIS DAY IN SPACE WEATHER HISTORY (60-90 seconds, ~100-150 words): A SHORT, ENTERTAINING segment about a historical space weather event that happened on this date. Keep it brief and fun — like a "on this day in history" radio segment. Use the provided This Day in History data. If no data is available, skip this segment.

4. SPACE WEATHER REPORT (5-7 minutes, ~800-1000 words): Cover today's live space weather data conversationally. Discuss the Kp index, solar wind, Bz, flare activity, and aurora forecast. Explain what the numbers mean in plain English. Ollie should ask rhetorical questions and then answer them, as if thinking out loud.

5. BLOG ARTICLES (5-7 minutes, ~800-1000 words): Discuss the day's blog articles. Summarize key points, add insights, and share personal takeaways. Make it conversational, not a reading of the article.

6. SPACE EVENTS (5-7 minutes, ~800-1000 words): Cover recent space events — geomagnetic storms, solar flares, asteroid approaches, etc. Discuss what happened, why it matters, and who's affected.

7. FACT CHECKS (3-5 minutes, ~500-700 words): Cover recent fact checks. Discuss the claims and verdicts. Explain why the claim is true, false, or somewhere in between.

8. DAILY BRIEF (3-5 minutes, ~500-700 words): Discuss the daily highlight/brief article. Cover the key points and implications.

9. CALL TO ACTIONS (1-2 minutes, ~150-200 words): Include these CTAs:
   - Visit flarient.com for live space weather data, real time Kp index, aurora forecasts, and interactive dashboards — it is the official website of Flarient and the best place to see what is happening right now
   - Subscribe to the podcast on your favorite platform so you never miss an episode
   - Visit flarient.com/podcast for all episodes and show notes
   - Follow Flarient on social media for real time space weather alerts
   - Join the Flarient community for aurora chasers and space weather enthusiasts
   - Download the Flarient app for push notifications when geomagnetic storms hit

IMPORTANT RULES:
- Make it CONVERSATIONAL and NATURAL — Ollie is a single host speaking directly to the listener. Use a warm, engaging tone as if talking to a friend. Add personal insights, rhetorical questions, and humor.
- Do NOT just read facts — discuss them, explain them, make them accessible to a general audience
- Use varied sentence structures and natural speech patterns (fillers like "right", "exactly", "I mean" are OK sparingly)
- Include moments of personality, humor, and genuine curiosity
- The HOOK must be ORIGINAL and DIFFERENT every episode — never repeat the same opening
- The THIS DAY IN HISTORY segment must be SHORT (60-90 seconds max) and ENTERTAINING — not a dry history lesson
- Each segment should flow naturally into the next
- Aim for approximately 4,500-5,000 words total for daily (30 min) or 6,500-7,000 words for weekly (45 min)

CONTENT DATA:
{live_summary}{events_summary}{articles_summary}{fact_checks_summary}{brief_summary}{this_day_summary}

Respond with a JSON object with this exact structure:
{{
  "title": "Episode title (max 120 chars, engaging and descriptive)",
  "hook": "The full 30-60 second opening hook (as a single segment from Ollie)",
  "show_notes": "Detailed show notes with key topics covered, links to articles/events mentioned, and timestamps. Format as plain text with line breaks.",
  "segments": [
    {{"speaker": "A", "text": "dialogue text for this segment", "section": "hook|intro|this_day|space_weather|articles|events|fact_checks|brief|ctas"}},
    ...
  ],
  "ctas": ["CTA 1 text", "CTA 2 text", ...]
}}

The "hook" field should be the first segment of the podcast (Ollie's opening). Include it as the first element in segments as well.
The "segments" array should contain ALL dialogue including the hook, intro, all content sections, and CTAs.
Each segment text should be 1-5 sentences (natural dialogue chunks).
Each segment MUST have a "section" field indicating which part of the show it belongs to.
All segments should have "speaker": "A" (Ollie is the only host).
Generate enough segments to fill the target duration."""


def generate_script(content, episode_type):
    log(f"Generating podcast script with Gemini (type: {episode_type})...")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = build_prompt(content, episode_type)

    # Try runtime-discovered models first, fall back to hardcoded list
    models_to_try = discover_gemini_models(client) or GEMINI_MODELS
    log(f"  Model chain: {', '.join(models_to_try)}")

    for model_name in models_to_try:
        try:
            log(f"  Trying model: {model_name}")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=16384,
                ),
            )
            text = response.text
            result = json.loads(text)
            word_count = sum(len(s.get("text", "").split()) for s in result.get("segments", []))
            log(f"  Success! {len(result.get('segments', []))} segments, ~{word_count} words")
            if word_count < 2000:
                log(f"  WARNING: Script is short ({word_count} words). May be less than 15 minutes.")
            return result
        except Exception as e:
            log(f"  Model {model_name} failed: {e}")
            continue

    raise Exception("All Gemini models failed. Check GEMINI_API_KEY and quota.")


# ── 3. Synthesize speech with edge-tts ─────────────────────────────────────
async def synth_segment(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


async def synthesize_all(segments):
    log(f"Synthesizing {len(segments)} segments with edge-tts...")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(5)

    async def synth_one(i, seg):
        async with sem:
            output = str(WORK_DIR / f"segment_{i:04d}.mp3")
            await synth_segment(seg["text"], HOST_VOICE, output)
            return output

    tasks = [synth_one(i, seg) for i, seg in enumerate(segments)]
    segment_files = await asyncio.gather(*tasks)
    log(f"  All segments synthesized: {len(segment_files)} files")
    return segment_files


# ── 4. Generate intro/outro jingles with ffmpeg ───────────────────────────
def generate_jingle(output_path, ascending=True):
    freqs = [523, 659, 784, 1047] if ascending else [1047, 784, 659, 523]
    inputs = []
    for f in freqs:
        inputs.extend(["-f", "lavfi", "-i", f"sine=frequency={f}:duration=0.35"])
    n = len(freqs)
    # Combine concat, fade, and volume into one filter_complex (avoid -af + -filter_complex conflict)
    filter_str = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[concat];[concat]afade=t=out:st=1.2:d=0.3,volume=0.6[a]"
    try:
        subprocess.run([
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_str,
            "-map", "[a]",
            "-t", "1.5",
            output_path
        ], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log(f"  Jingle generation failed: {e.stderr[:500] if e.stderr else 'no stderr'}")
        raise


def get_jingles():
    intro_path = str(WORK_DIR / "intro.mp3")
    outro_path = str(WORK_DIR / "outro.mp3")
    user_intro = REPO_DIR / "assets" / "intro.mp3"
    user_outro = REPO_DIR / "assets" / "outro.mp3"
    if user_intro.exists():
        log("  Using user-provided intro jingle")
        subprocess.run(["cp", str(user_intro), intro_path], check=True)
    else:
        log("  Generating intro jingle")
        generate_jingle(intro_path, ascending=True)
    if user_outro.exists():
        log("  Using user-provided outro jingle")
        subprocess.run(["cp", str(user_outro), outro_path], check=True)
    else:
        log("  Generating outro jingle")
        generate_jingle(outro_path, ascending=False)
    return intro_path, outro_path


# ── 4b. Generate background music bed ─────────────────────────────────────
def generate_music_bed(output_path, duration_sec):
    """Generate a royalty-free ambient music bed with ffmpeg (sine waves + reverb)."""
    log("  Generating background music bed...")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=110:duration=" + str(duration_sec),
        "-f", "lavfi", "-i", "sine=frequency=165:duration=" + str(duration_sec),
        "-f", "lavfi", "-i", "sine=frequency=220:duration=" + str(duration_sec),
        "-filter_complex",
        "[0:a]volume=0.08[a1];[1:a]volume=0.06[a2];[2:a]volume=0.04[a3];"
        "[a1][a2][a3]amix=inputs=3:duration=longest[mix];"
        "[mix]aecho=0.6:0.3:500|0.4:0.2|1000[echo];"
        "[echo]lowpass=f=800[bed]",
        "-map", "[bed]",
        "-ar", "44100", "-ab", "128k",
        output_path
    ], check=True, capture_output=True)


# ── 4c. Generate per-episode cover art with Pillow ────────────────────────
def generate_cover_art(episode_type, content):
    """Generate a 3000x3000 cover art image with the date, Kp index, and episode type."""
    log("  Generating cover art...")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("  Pillow not available, skipping cover art")
        return None

    W, H = 3000, 3000
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # Dark space gradient background
    for y in range(H):
        r = int(10 + (y / H) * 20)
        g = int(6 + (y / H) * 10)
        b = int(32 + (y / H) * 40)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Add some "stars"
    import random
    random.seed(EPISODE_DATE)
    for _ in range(200):
        x, y = random.randint(0, W), random.randint(0, H)
        brightness = random.randint(80, 255)
        size = random.choice([1, 1, 1, 2, 2, 3])
        draw.ellipse([x-size, y-size, x+size, y+size], fill=(brightness, brightness, brightness))

    # Draw a stylized sun/circle
    cx, cy, cr = W // 2, 1100, 300
    for i in range(cr, 0, -2):
        alpha = int(80 * (1 - i / cr))
        color = (99 + alpha, 102 + alpha, 241)
        draw.ellipse([cx-i, cy-i, cx+i, cy+i], outline=color)

    # Try to load a font
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 120)
        font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 60)
    except:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Episode type badge
    type_colors = {"daily": (99, 102, 241), "weekly": (245, 158, 11), "breaking": (239, 68, 68)}
    badge_color = type_colors.get(episode_type, (99, 102, 241))
    badge_text = episode_type.upper()
    bbox = draw.textbbox((0, 0), badge_text, font=font_medium)
    bw = bbox[2] - bbox[0] + 60
    bh = bbox[3] - bbox[1] + 30
    bx = (W - bw) // 2
    by = 200
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=20, fill=badge_color)
    draw.text((bx + 30, by + 10), badge_text, fill="white", font=font_medium)

    # Date
    date_text = EPISODE_DATE
    bbox = draw.textbbox((0, 0), date_text, font=font_large)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, 1600), date_text, fill="white", font=font_large)

    # Kp index
    live = content.get("live_data") or {}
    kp = live.get("kp_index") or live.get("kp") or "?"
    kp_text = f"Kp {kp}"
    bbox = draw.textbbox((0, 0), kp_text, font=font_medium)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, 1800), kp_text, fill=(34, 211, 238), font=font_medium)

    # Flarient branding
    brand_text = "FLARIENT"
    bbox = draw.textbbox((0, 0), brand_text, font=font_large)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, 2500), brand_text, fill=(99, 102, 241), font=font_large)

    subtitle = "DAILY SPACE WEATHER"
    bbox = draw.textbbox((0, 0), subtitle, font=font_small)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, 2650), subtitle, fill=(200, 200, 220), font=font_small)

    cover_path = str(WORK_DIR / COVER_FILENAME)
    img.save(cover_path, "JPEG", quality=90)
    log(f"  Cover art saved: {cover_path}")
    return cover_path


# ── 5. Master audio with ffmpeg ───────────────────────────────────────────
def master_audio(segment_files, script, output_path):
    log("Mastering audio...")

    # 1. Concatenate all dialogue segments
    log("  Concatenating dialogue segments...")
    concat_list = WORK_DIR / "concat.txt"
    with open(concat_list, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    dialogue_raw = str(WORK_DIR / "dialogue_raw.mp3")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-ar", "44100", "-ab", "192k", "-ac", "2",
        dialogue_raw
    ], check=True, capture_output=True)

    # 2. Remove unnatural silences, normalize loudness, and add ID3 metadata in one pass
    #    silenceremove: strips pauses > 1.0s below -50dB (keeps natural speech pauses)
    #    loudnorm: broadcast standard -16 LUFS
    log("  Removing silence, normalizing loudness, and adding metadata...")
    title = script.get("title", f"Flarient Podcast — {EPISODE_DATE}")
    subprocess.run([
        "ffmpeg", "-y", "-i", dialogue_raw,
        "-af", "silenceremove=stop_periods=-1:stop_duration=1.0:stop_threshold=-50dB,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-metadata", f"title={title}",
        "-metadata", "artist=Flarient",
        "-metadata", "album=Flarient Daily Space Weather",
        "-metadata", "genre=Podcast",
        "-metadata", f"date={EPISODE_DATE}",
        "-ar", "44100", "-ab", "192k", "-ac", "2",
        output_path
    ], check=True, capture_output=True)

    log(f"  Final audio: {output_path}")


def generate_chapters_file(script, intro_dur=1.5):
    """Generate FFmpeg chapters metadata file from segment sections."""
    segments = script.get("segments", [])
    if not segments:
        return None

    # Group segments by section and estimate timings
    section_names = {
        "hook": "Hook", "intro": "Intro", "this_day": "This Day in History",
        "space_weather": "Space Weather Report", "articles": "Blog Articles",
        "events": "Space Events", "fact_checks": "Fact Checks",
        "brief": "Daily Brief", "ctas": "Call to Actions"
    }

    # Estimate ~150 words per minute (2.5 words/sec)
    current_time = intro_dur  # Start after intro jingle
    chapters = []
    seen_sections = set()

    for seg in segments:
        section = seg.get("section", "")
        if section and section not in seen_sections:
            seen_sections.add(section)
            chapters.append((current_time, section_names.get(section, section.title())))
        # Estimate segment duration from word count
        words = len(seg.get("text", "").split())
        current_time += words / 2.5  # ~150 wpm

    if not chapters:
        return None

    chapters_file = str(WORK_DIR / "chapters.txt")
    with open(chapters_file, "w") as f:
        f.write(";FFMETADATA1\n")
        for i, (start, title) in enumerate(chapters):
            end = chapters[i + 1][0] if i + 1 < len(chapters) else current_time
            start_ms = int(start * 1000)
            end_ms = int(end * 1000)
            f.write(f"[CHAPTER]\n")
            f.write(f"TIMEBASE=1/1000\n")
            f.write(f"START={start_ms}\n")
            f.write(f"END={end_ms}\n")
            f.write(f"title={title}\n")

    log(f"  Chapters file: {len(chapters)} chapters")
    return chapters_file


def get_duration(filepath):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", filepath
    ], capture_output=True, text=True, check=True)
    return int(float(result.stdout.strip()))


# ── 5b. Loudness check ────────────────────────────────────────────────────
def check_loudness(filepath):
    """Verify the final audio is within ±2 LUFS of -16."""
    log("  Checking loudness...")
    result = subprocess.run([
        "ffmpeg", "-i", filepath,
        "-af", "loudnorm=print_format=json",
        "-f", "null", "-"
    ], capture_output=True, text=True)

    # Parse loudness from stderr
    stderr = result.stderr
    match = re.search(r'"input_i"\s*:\s*"([\-\d.]+)"', stderr)
    if match:
        loudness = float(match.group(1))
        log(f"  Integrated loudness: {loudness:.1f} LUFS (target: -16 ±2)")
        if abs(loudness - (-16)) > 2:
            log(f"  WARNING: Loudness {loudness:.1f} LUFS is outside ±2 LUFS of target -16!")
            return False
        log("  Loudness check passed ✓")
        return True
    log("  Could not measure loudness (skipping check)")
    return True


# ── 5c. Save transcript ──────────────────────────────────────────────────
def save_transcript(script, output_path):
    """Save the full dialogue text as a transcript file."""
    log("  Saving transcript...")
    segments = script.get("segments", [])
    lines = [f"Flarient Daily Space Weather — {EPISODE_DATE}", ""]
    lines.append(f"Title: {script.get('title', 'Untitled')}", )
    lines.append(f"Episode Type: {EPISODE_TYPE}", )
    lines.append("", )
    lines.append("=" * 60, )
    lines.append("", )

    for seg in segments:
        lines.append(seg.get('text', ''))
        lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(str(l) for l in lines))
    log(f"  Transcript saved: {output_path}")


# ── 6. Create GitHub Release ──────────────────────────────────────────────
def create_release(mp3_path, script, cover_path=None, transcript_path=None):
    log("Creating GitHub Release...")
    title = script.get("title", f"Flarient Podcast — {EPISODE_DATE}")
    type_prefix = {"weekly": "[WEEKLY] ", "breaking": "[BREAKING] "}.get(EPISODE_TYPE, "")
    full_title = f"{type_prefix}{title}"
    notes = f"""Episode Type: {EPISODE_TYPE.upper()}
Date: {EPISODE_DATE}

{script.get("show_notes", f"Flarient Daily Space Weather Podcast for {EPISODE_DATE}")}"""

    env = os.environ.copy()
    env["GH_TOKEN"] = GH_TOKEN

    # Build asset list
    assets = [mp3_path]
    if cover_path:
        assets.append(cover_path)
    if transcript_path:
        assets.append(transcript_path)

    result = subprocess.run([
        "gh", "release", "create", EPISODE_TAG,
        *assets,
        "--title", full_title,
        "--notes", notes,
    ], capture_output=True, text=True, env=env)

    if result.returncode != 0:
        if "already exists" in result.stderr.lower():
            log("  Release already exists, deleting and recreating...")
            subprocess.run(["gh", "release", "delete", EPISODE_TAG, "--yes"], env=env, capture_output=True)
            subprocess.run(["git", "push", "origin", "--delete", EPISODE_TAG], env=env, capture_output=True)
            result = subprocess.run([
                "gh", "release", "create", EPISODE_TAG,
                *assets,
                "--title", full_title,
                "--notes", notes,
            ], capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise Exception(f"Failed to create release: {result.stderr}")

    owner_repo = REPO
    mp3_url = f"https://github.com/{owner_repo}/releases/download/{EPISODE_TAG}/{MP3_FILENAME}"
    cover_url = f"https://github.com/{owner_repo}/releases/download/{EPISODE_TAG}/{COVER_FILENAME}" if cover_path else None
    transcript_url = f"https://github.com/{owner_repo}/releases/download/{EPISODE_TAG}/{TRANSCRIPT_FILENAME}" if transcript_path else None
    log(f"  Release created: {mp3_url}")
    return mp3_url, cover_url, transcript_url


# ── 7. Update podcast.xml RSS feed ────────────────────────────────────────
def escape_xml(text):
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def update_rss_feed(script, mp3_url, cover_url, duration_sec, file_size):
    log("Updating podcast.xml RSS feed...")
    rss_path = REPO_DIR / "podcast.xml"
    episode_guid = f"flarient-podcast-{EPISODE_DATE}"
    pub_date = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    type_prefix = {"weekly": "[WEEKLY] ", "breaking": "[BREAKING] "}.get(EPISODE_TYPE, "")
    full_title = f"{type_prefix}{script.get('title', f'Daily Space Podcast — {EPISODE_DATE}')}"
    # Generate title-based slug for the episode URL
    episode_slug = re.sub(r'[^a-z0-9 \s-]', '', script.get('title', '').lower()).strip()
    episode_slug = re.sub(r'[\s-]+', '-', episode_slug).strip('-')[:80] or EPISODE_DATE
    episode_link = f"https://flarient.com/podcast/{episode_slug}"
    # Count existing episodes for the episode number
    episode_number = 1
    if rss_path.exists():
        existing_xml = rss_path.read_text(encoding="utf-8")
        episode_number = existing_xml.count("<item>") + 1

    cover_tag = f"      <itunes:image href='{escape_xml(cover_url)}'/>\n" if cover_url else ""

    episode_xml = f"""    <item>
       <title>{escape_xml(full_title)}</title>
       <link>{episode_link}</link>
       <description>{escape_xml(script.get("show_notes", ""))}</description>
       <pubDate>{pub_date}</pubDate>
       <guid isPermaLink="false">{episode_guid}</guid>
       <enclosure url="{escape_xml(mp3_url)}" length="{file_size}" type="audio/mpeg"/>
       <itunes:duration>{duration_sec}</itunes:duration>
       <itunes:episodeType>{EPISODE_TYPE}</itunes:episodeType>
       <itunes:episode>{episode_number}</itunes:episode>
{cover_tag}       <itunes:summary>{escape_xml(script.get("show_notes", ""))}</itunes:summary>
     </item>
"""

    if rss_path.exists():
        xml = rss_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r'\s*<item>.*?' + re.escape(episode_guid) + r'.*?</item>\s*',
            re.DOTALL
        )
        xml = pattern.sub('\n', xml)
        # Update channel-level itunes:image to latest cover and add missing tags
        if cover_url and "<item>" in xml:
            channel_part, items_part = xml.split("<item>", 1)
            channel_part = re.sub(
                r"""<itunes:image href=["'][^"']*["'] */?>""",
                f'<itunes:image href="{escape_xml(cover_url)}"/>',
                channel_part
            )
            if "<itunes:type>" not in channel_part:
                channel_part = channel_part.replace(
                    "<itunes:explicit>false</itunes:explicit>",
                    "<itunes:explicit>false</itunes:explicit>\n    <itunes:type>episodic</itunes:type>"
                )
            xml = channel_part + "<item>" + items_part
        if "</channel>" in xml:
            xml = xml.replace("</channel>", episode_xml + "  </channel>")
        else:
            log("  WARNING: Could not find </channel> in existing feed")
            return
    else:
        channel_cover = cover_url or PODCAST_COVER
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape_xml(PODCAST_TITLE)}</title>
    <link>{PODCAST_LINK}</link>
    <language>en-us</language>
    <description>{escape_xml(PODCAST_DESC)}</description>
    <itunes:summary>{escape_xml(PODCAST_DESC)}</itunes:summary>
    <itunes:author>{escape_xml(PODCAST_AUTHOR)}</itunes:author>
    <itunes:type>episodic</itunes:type>
    <itunes:category text="{PODCAST_CATEGORY}"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{escape_xml(channel_cover)}"/>
    <itunes:owner>
      <itunes:name>{escape_xml(PODCAST_AUTHOR)}</itunes:name>
      <itunes:email>podcast@flarient.com</itunes:email>
    </itunes:owner>
{episode_xml}  </channel>
</rss>"""

    rss_path.write_text(xml, encoding="utf-8")
    log("  podcast.xml updated")


def commit_feed():
    log("Committing changes to repo...")
    env = os.environ.copy()
    env["GH_TOKEN"] = GH_TOKEN
    subprocess.run(["git", "config", "user.name", "Daily Space Podcast Bot"], env=env, check=True)
    subprocess.run(["git", "config", "user.email", "bot@flarient.com"], env=env, check=True)

    # Add podcast.xml and covered_content.json
    subprocess.run(["git", "add", "podcast.xml"], env=env, check=True, cwd=str(REPO_DIR))
    subprocess.run(["git", "add", "covered_content.json"], env=env, capture_output=True, cwd=str(REPO_DIR))

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], env=env, cwd=str(REPO_DIR))
    if result.returncode == 0:
        log("  No changes to commit")
        return

    subprocess.run(["git", "commit", "-m", f"Podcast episode {EPISODE_DATE} ({EPISODE_TYPE})"], env=env, check=True, cwd=str(REPO_DIR))
    subprocess.run(["git", "push"], env=env, check=True, cwd=str(REPO_DIR))
    log("  Changes committed and pushed")


def enable_github_pages():
    log("Ensuring GitHub Pages is enabled...")
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Try to create Pages site (uses requests, not gh CLI, for reliability)
    resp = requests.post(
        f"https://api.github.com/repos/{REPO}/pages",
        headers=headers,
        json={"source": {"branch": "main", "path": "/"}}
    )
    if resp.status_code == 201:
        log("  GitHub Pages enabled")
    elif resp.status_code == 409:
        log("  GitHub Pages already enabled")
    else:
        log(f"  GitHub Pages setup: {resp.status_code} {resp.text[:200]}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log(f"=== Daily Space Podcast Generator — {EPISODE_DATE} (type: {EPISODE_TYPE}) ===")

    if not GEMINI_API_KEY:
        log("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)

    # Check if episode already exists (one per day max)
    if release_exists():
        log("Episode already exists for today — skipping (one per day max)")
        sys.exit(0)

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Fetch content
    content = fetch_content()

    # 1b. Dedup: load covered content and filter
    covered = load_covered()
    content = filter_covered(content, covered)

    # 2. Generate script
    script = generate_script(content, EPISODE_TYPE)
    log(f"  Title: {script.get('title', 'Untitled')}")

    # 3. Synthesize speech
    segments = script.get("segments", [])
    if not segments:
        log("ERROR: No segments generated")
        sys.exit(1)
    segment_files = asyncio.run(synthesize_all(segments))

    # 4. Generate cover art
    cover_path = generate_cover_art(EPISODE_TYPE, content)

    # 5. Master audio (silence removal + loudness normalization + metadata in one pass)
    final_mp3 = str(WORK_DIR / MP3_FILENAME)
    master_audio(segment_files, script, final_mp3)

    # 5b. Save transcript
    transcript_path = str(WORK_DIR / TRANSCRIPT_FILENAME)
    save_transcript(script, transcript_path)

    # 6. Get duration and file size
    duration_sec = get_duration(final_mp3)
    file_size = Path(final_mp3).stat().st_size
    log(f"  Duration: {duration_sec}s ({duration_sec // 60}m {duration_sec % 60}s)")
    log(f"  File size: {file_size / 1024 / 1024:.1f} MB")

    # 7. Create GitHub Release (with MP3, cover art, transcript)
    mp3_url, cover_url, transcript_url = create_release(final_mp3, script, cover_path, transcript_path)

    # 8. Update covered content
    new_ids = content.get("_new_covered_ids", [])
    if new_ids:
        save_covered(covered, new_ids)

    # 9. Update RSS feed
    update_rss_feed(script, mp3_url, cover_url, duration_sec, file_size)

    # 10. Commit changes
    commit_feed()

    # 11. Enable GitHub Pages
    enable_github_pages()

    # Summary
    log("=== PODCAST PUBLISHED ===")
    log(f"  Episode: {script.get('title', 'Untitled')}")
    log(f"  Type: {EPISODE_TYPE}")
    log(f"  MP3 URL: {mp3_url}")
    if cover_url:
        log(f"  Cover art: {cover_url}")
    if transcript_url:
        log(f"  Transcript: {transcript_url}")
    log(f"  Duration: {duration_sec // 60}m {duration_sec % 60}s")
    log(f"  RSS feed: podcast.xml (committed to repo)")
    owner = REPO.split('/')[0]
    repo_name = REPO.split('/')[1]
    log(f"  RSS URL (GitHub Pages): https://{owner}.github.io/{repo_name}/podcast.xml")
    log("  Submit this RSS URL to: Spotify for Podcasters, Apple Podcasts Connect, Amazon Music")


if __name__ == "__main__":
    main()
