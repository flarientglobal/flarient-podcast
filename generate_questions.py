#!/usr/bin/env python3
"""
Podcast Question Page Generator — turns each episode into 10-30 Google-searchable Q&A pages.
Zero cost: Gemini free tier + GitHub Pages for hosting + IndexNow for instant indexing.
Each page: Question → Concise answer → Relevant data → Episode segment → Listen from timestamp → Related Flarient intelligence.
Also extracts episode genome topics for the Evergreen Reactivation Engine.
"""

import os, sys, json, re, subprocess, datetime, hashlib
from pathlib import Path
import requests

FLARIENT_API = os.environ.get("FLARIENT_API_URL", "https://flarient.com").rstrip("/")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))
QUESTIONS_DIR = REPO_DIR / "questions"
INDEXNOW_KEY = os.environ.get("INDEXNOW_KEY", "flarientpodcast2026indexnowkeya8f3b2e1d4c7")
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
SITE_BASE = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}" if REPO else ""

def log(msg):
    print(f"[QUESTIONS] {msg}", flush=True)

def gh_api(path, method="GET", body=None):
    url = f"https://api.github.com{path}" if path.startswith("/") else path
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    resp = requests.request(method, url, headers=headers, json=body)
    return resp

def get_latest_release():
    """Get the latest podcast release with its assets."""
    resp = subprocess.run(["gh", "release", "list", "--limit", "1", "--json", "tagName,assets,body"], capture_output=True, text=True, env={**os.environ, "GH_TOKEN": GH_TOKEN})
    if resp.returncode != 0:
        return None
    releases = json.loads(resp.stdout)
    if not releases:
        return None
    return releases[0]

def download_asset(tag, filename, dest):
    """Download a release asset to a local path."""
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    result = subprocess.run(["gh", "release", "download", tag, "--pattern", filename, "--dir", str(dest)], capture_output=True, text=True, env=env)
    return result.returncode == 0

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text[:80]

def generate_questions(transcript, episode_title, episode_date):
    """Use Gemini to generate 10-30 questions with answers from the transcript."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""You are generating Google-searchable Q&A pages from a podcast episode transcript.

EPISODE: "{episode_title}" (Date: {episode_date})

TRANSCRIPT (first 8000 chars):
{transcript[:8000]}

Generate 10-30 questions that:
1. Are genuinely useful questions people would search on Google
2. Can be answered substantively from the episode content (NOT thin SEO spam)
3. Cover diverse aspects: definitions, how-things-work, predictions, comparisons, data interpretation
4. Are phrased naturally as questions (e.g. "Can AI predict solar flares?", "What is a Brier score?")
5. Each answer must be 150-300 words — substantive enough to properly answer the question

For each question also provide:
- answer: 150-300 word substantive answer based on the episode content
- segment_quote: a 1-2 sentence quote from the episode that covers this topic
- segment_timestamp: estimated timestamp in seconds (based on word position in transcript)
- flarient_links: 1-3 relevant Flarient.com page URLs (e.g. /kp-index, /aurora-forecast, /solar-flares, /podcast, /blog)
- genome_topics: 1-3 topic tags from this list: x_class_flare, kp_7_plus, g3_storm, earth_directed_cme, major_aurora, close_asteroid, ai_vs_human, record_solar_wind, major_sunspot, solar_flare, geomagnetic_storm, aurora, asteroid, cme, solar_wind, radio_blackout, hf_radio, gnss, satellite, space_weather

Respond as JSON:
{{
  "questions": [
    {{
      "question": "Can AI predict solar flares?",
      "answer": "150-300 word answer...",
      "segment_quote": "Quote from the episode...",
      "segment_timestamp": 1042,
      "flarient_links": ["/solar-flares", "/blog"],
      "genome_topics": ["solar_flare", "ai_vs_human"]
    }}
  ]
}}

IMPORTANT: Every answer must be substantive (150+ words). Do NOT generate thin or generic content. If the transcript doesn't have enough substance for a question, don't include that question."""

    for model_name in GEMINI_MODELS:
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
            result = json.loads(response.text)
            questions = result.get("questions", [])
            log(f"  Generated {len(questions)} questions")
            return questions
        except Exception as e:
            log(f"  Model {model_name} failed: {e}")
            continue
    raise Exception("All Gemini models failed for question generation")

def build_question_html(q, episode_title, episode_date, mp3_url, episode_slug):
    """Build a standalone HTML page for one question."""
    slug = slugify(q["question"])
    timestamp = q.get("segment_timestamp", 0)
    listen_url = f"{mp3_url}#t={timestamp}" if timestamp > 0 else mp3_url
    mins = int(timestamp // 60)
    secs = int(timestamp % 60)
    listen_from = f"{mins}:{secs:02d}"

    flarient_links_html = ""
    for link in q.get("flarient_links", []):
        url = f"https://flarient.com{link}" if link.startswith("/") else link
        label = link.strip("/").replace("-", " ").title() or "Flarient"
        flarient_links_html += f'<a href="{url}" class="flink">{label} →</a>\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{q["question"]} | Flarient Podcast</title>
<meta name="description" content="{q["answer"][:150]}...">
<meta name="robots" content="index, follow">
<meta property="og:title" content="{q["question"]}">
<meta property="og:description" content="{q["answer"][:150]}...">
<meta property="og:type" content="article">
<link rel="canonical" href="{SITE_BASE}/questions/{slug}.html">
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{{"@type":"Question","name":"{q["question"]}","acceptedAnswer":{{"@type":"Answer","text":"{q["answer"][:500]}"}}}}]}}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0620;color:#e8eaf2;line-height:1.7;max-width:720px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:1.6rem;font-weight:700;margin:24px 0 16px;line-height:1.3;color:#fff}}
.answer{{font-size:1.05rem;color:rgba(255,255,255,0.75);margin:16px 0 24px}}
.answer p{{margin-bottom:14px}}
.section{{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:16px;margin:16px 0}}
.section h2{{font-size:0.85rem;text-transform:uppercase;letter-spacing:0.05em;color:rgba(255,255,255,0.4);margin-bottom:10px;font-weight:600}}
.listen-btn{{display:inline-flex;align-items:center;gap:8px;background:#6366f1;color:#fff;padding:12px 20px;border-radius:9999px;font-size:0.9rem;font-weight:600;text-decoration:none;margin:8px 0}}
.listen-btn:hover{{background:#818cf8}}
.timestamp{{color:#22d3ee;font-weight:600}}
.flinks{{display:flex;flex-direction:column;gap:8px}}
.flink{{color:#818cf8;text-decoration:none;font-size:0.9rem;padding:10px 14px;background:rgba(99,102,241,0.08);border-radius:8px;border:1px solid rgba(99,102,241,0.15)}}
.flink:hover{{background:rgba(99,102,241,0.15)}}
.brand{{text-align:center;margin-top:40px;padding-top:20px;border-top:1px solid rgba(255,255,255,0.08)}}
.brand a{{color:#818cf8;text-decoration:none;font-weight:600}}
.episode-meta{{font-size:0.8rem;color:rgba(255,255,255,0.4);margin-bottom:8px}}
</style>
</head>
<body>
<article>
<h1>{q["question"]}</h1>
<div class="answer"><p>{q["answer"]}</p></div>

<div class="section">
<h2>Episode Segment</h2>
<p class="episode-meta">From: {episode_title} ({episode_date})</p>
<blockquote style="border-left:3px solid #6366f1;padding-left:12px;color:rgba(255,255,255,0.6);font-style:italic;margin:8px 0">{q.get("segment_quote", "")}</blockquote>
<a href="{listen_url}" class="listen-btn" target="_blank" rel="noreferrer">▶ Listen from {listen_from}</a>
</div>

<div class="section">
<h2>Related Flarient Intelligence</h2>
<div class="flinks">
{flarient_links_html}
</div>
</div>

<div class="brand">
<p>Generated from the <a href="https://flarient.com/podcast">Daily Space Podcast by Flarient</a></p>
<p style="font-size:0.8rem;color:rgba(255,255,255,0.3);margin-top:4px"><a href="https://flarient.com">flarient.com</a> — Space Decision Intelligence</p>
</div>
</article>
</body>
</html>"""

def update_sitemap(question_slugs):
    """Update the questions sitemap.xml."""
    urls = [f"{SITE_BASE}/questions/{slug}.html" for slug in question_slugs]
    today = datetime.date.today().isoformat()
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url in urls:
        xml += f"  <url><loc>{url}</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>\n"
    xml += '</urlset>'
    (QUESTIONS_DIR / "sitemap.xml").write_text(xml)
    log(f"  Sitemap updated: {len(urls)} URLs")

def send_indexnow(urls):
    """Notify IndexNow of new/updated URLs."""
    if not SITE_BASE:
        return
    try:
        resp = requests.post(
            "https://api.indexnow.org/indexnow",
            json={
                "host": SITE_BASE.split("//")[1].split("/")[0],
                "key": INDEXNOW_KEY,
                "urlList": urls,
            },
            timeout=10,
        )
        log(f"  IndexNow: {resp.status_code} ({len(urls)} URLs)")
    except Exception as e:
        log(f"  IndexNow failed: {e}")

def update_episode_genomes(episode_date, genome_topics):
    """Update episode_genomes.json with the episode's genome topics."""
    genomes_path = REPO_DIR / "episode_genomes.json"
    genomes = {}
    if genomes_path.exists():
        try:
            genomes = json.loads(genomes_path.read_text())
        except:
            pass
    genomes[episode_date] = list(set(genomes))
    genomes_path.write_text(json.dumps(genomes, indent=2))
    log(f"  episode_genomes.json updated ({len(genomes)} episodes)")

def commit_changes():
    """Commit question pages, sitemap, and genomes to the repo."""
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    subprocess.run(["git", "config", "user.name", "Podcast Growth Bot"], env=env, check=True)
    subprocess.run(["git", "config", "user.email", "growth@flarient.com"], env=env, check=True)
    subprocess.run(["git", "add", "questions/"], env=env, cwd=str(REPO_DIR), check=True)
    subprocess.run(["git", "add", "episode_genomes.json"], env=env, cwd=str(REPO_DIR), capture_output=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], env=env, cwd=str(REPO_DIR))
    if result.returncode == 0:
        log("  No changes to commit")
        return
    subprocess.run(["git", "commit", "-m", "Generate question pages from latest episode"], env=env, check=True, cwd=str(REPO_DIR))
    import time
    for attempt in range(3):
        try:
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
            subprocess.run(["git", "push", "origin", "HEAD:main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
            log("  Changes committed and pushed")
            return
        except subprocess.CalledProcessError as e:
            log(f"  Push attempt {attempt+1}/3 failed: {(e.stderr or '')[:200]}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
            else:
                subprocess.run(["git", "push", "--force-with-lease", "origin", "HEAD:main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
                log("  Pushed (force-with-lease)")

def main():
    log("=== Podcast Question Page Generator ===")
    if not GEMINI_API_KEY:
        log("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)

    release = get_latest_release()
    if not release:
        log("No releases found — skipping")
        sys.exit(0)

    tag = release["tagName"]
    log(f"Latest release: {tag}")

    # Download transcript
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    episode_date = tag.replace("podcast-", "")
    transcript_filename = f"transcript-{episode_date}.txt"
    if not download_asset(tag, transcript_filename, QUESTIONS_DIR):
        log(f"No transcript found ({transcript_filename}) — skipping")
        sys.exit(0)

    transcript_path = QUESTIONS_DIR / transcript_filename
    transcript = transcript_path.read_text()

    # Get episode title from release body
    episode_title = release.get("body", "").split("\n")[0] or f"Flarient Podcast — {episode_date}"
    # Clean title (remove type prefix)
    episode_title = re.sub(r'^\[(WEEKLY|BREAKING)\]\s*', '', episode_title).strip()

    # Get MP3 URL
    mp3_assets = [a for a in release.get("assets", []) if a["name"].endswith(".mp3")]
    mp3_url = mp3_assets[0]["url"] if mp3_assets else ""
    if not mp3_url:
        mp3_url = f"https://github.com/{REPO}/releases/download/{tag}/flarient-podcast-{episode_date}.mp3"

    episode_slug = slugify(episode_title)

    # Generate questions
    questions = generate_questions(transcript, episode_title, episode_date)
    if not questions:
        log("No questions generated — skipping")
        sys.exit(0)

    # Build question pages
    question_slugs = []
    all_genome_topics = []
    new_urls = []
    for q in questions:
        slug = slugify(q["question"])
        html = build_question_html(q, episode_title, episode_date, mp3_url, episode_slug)
        (QUESTIONS_DIR / f"{slug}.html").write_text(html)
        question_slugs.append(slug)
        all_genome_topics.extend(q.get("genome_topics", []))
        new_urls.append(f"{SITE_BASE}/questions/{slug}.html")
        log(f"  Created: {slug}.html")

    # Build index page
    index_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flarient Podcast Questions</title>
<style>body{{font-family:system-ui;background:#0a0620;color:#e8eaf2;max-width:720px;margin:0 auto;padding:20px}}
a{{color:#818cf8;text-decoration:none}}a:hover{{color:#a5b4fc}}h1{{margin-bottom:20px}}</style>
</head><body><h1>Flarient Podcast — Search Questions</h1>
<p style="color:rgba(255,255,255,0.5);margin-bottom:20px">Generated from episode: {episode_title} ({episode_date})</p>
<ul style="list-style:none;padding:0">"""
    for slug in question_slugs:
        q_text = next((q["question"] for q in questions if slugify(q["question"]) == slug), slug)
        index_html += f'<li style="margin:8px 0"><a href="{slug}.html">{q_text}</a></li>\n'
    index_html += "</ul></body></html>"
    (QUESTIONS_DIR / "index.html").write_text(index_html)

    # Update sitemap
    update_sitemap(question_slugs)

    # Send IndexNow notification
    send_indexnow(new_urls)

    # Update episode genomes
    update_episode_genomes(episode_date, all_genome_topics)

    # Commit changes
    commit_changes()

    # Cleanup transcript
    transcript_path.unlink(missing_ok=True)

    log(f"=== DONE: {len(questions)} question pages generated ===")

if __name__ == "__main__":
    main()
