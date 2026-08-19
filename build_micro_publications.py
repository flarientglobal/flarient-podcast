#!/usr/bin/env python3
"""
Builds Flarient Micro Publications — lightweight content lens pages on GitHub Pages.
Each lens dynamically shows the latest relevant content for its domain, linking back to Flarient.com.
Lenses: Aurora Intelligence, Solar Flare Intelligence, Asteroid Intelligence, Human vs AI Forecasting.
Zero cost: GitHub Pages + Flarient API.
"""

import os, sys, json, re, subprocess, datetime
from pathlib import Path
import requests

FLARIENT_API = os.environ.get("FLARIENT_API_URL", "https://flarient.com").rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))
MICRO_DIR = REPO_DIR / "micro-publications"

def log(msg):
    print(f"[MICRO-PUBS] {msg}", flush=True)

def fetch_json(path):
    """Fetch JSON from the Flarient API."""
    try:
        resp = requests.get(f"{FLARIENT_API}/api/functions/{path}", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"  Failed to fetch {path}: {e}")
        return None

def fetch_rss_episodes(limit=10):
    """Fetch recent episodes from the podcast RSS feed."""
    rss_url = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}/podcast.xml"
    try:
        resp = requests.get(rss_url, timeout=10)
        resp.raise_for_status()
        xml = resp.text
        episodes = []
        for match in re.finditer(r'<item>(.*?)</item>', xml, re.DOTALL):
            item = match.group(1)
            title_m = re.search(r'<title>(.*?)</title>', item)
            desc_m = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
            enc_m = re.search(r'<enclosure[^>]*url="([^"]*)"', item)
            dur_m = re.search(r'<itunes:duration>(.*?)</itunes:duration>', item)
            if title_m:
                episodes.append({
                    "title": title_m.group(1),
                    "description": (desc_m.group(1) if desc_m else "")[:200],
                    "mp3_url": enc_m.group(1) if enc_m else "",
                    "duration": dur_m.group(1) if dur_m else "",
                })
            if len(episodes) >= limit:
                break
        return episodes
    except Exception as e:
        log(f"  Failed to fetch RSS: {e}")
        return []

def fetch_events():
    """Fetch recent space events from Flarient."""
    data = fetch_json("getPodcastContent")
    if not data:
        return []
    events = data.get("events", []) or []
    return events[:10]

def fetch_live_data():
    """Fetch live space weather data from Flarient."""
    data = fetch_json("getLiveSpaceWeather")
    return data or {}

def build_lens_page(lens_config, episodes, events, live_data):
    """Build a single micro publication HTML page."""
    lens_id = lens_config["id"]
    lens_title = lens_config["title"]
    lens_desc = lens_config["description"]
    lens_color = lens_config["color"]
    lens_icon = lens_config["icon"]
    match_keywords = lens_config["match_keywords"]

    # Filter episodes by keywords
    relevant_episodes = []
    for ep in episodes:
        title_lower = ep["title"].lower()
        desc_lower = ep.get("description", "").lower()
        if any(kw in title_lower or kw in desc_lower for kw in match_keywords):
            relevant_episodes.append(ep)
    if not relevant_episodes:
        relevant_episodes = episodes[:3]

    # Filter events by type
    relevant_events = []
    for ev in events:
        ev_type = (ev.get("event_type") or "").lower()
        ev_title = (ev.get("title") or "").lower()
        if any(kw in ev_type or kw in ev_title for kw in match_keywords):
            relevant_events.append(ev)
    if not relevant_events:
        relevant_events = events[:5]

    # Build episodes HTML
    episodes_html = ""
    for ep in relevant_episodes[:3]:
        episodes_html += f"""
        <a href="https://flarient.com/podcast" class="card">
          <div class="card-icon">🎧</div>
          <div>
            <div class="card-title">{ep['title']}</div>
            <div class="card-desc">{ep.get('description', '')[:120]}</div>
          </div>
        </a>"""

    # Build events HTML
    events_html = ""
    for ev in relevant_events[:3]:
        severity = ev.get("severity", "")
        events_html += f"""
        <a href="https://flarient.com/space-events" class="card">
          <div class="card-icon">⚡</div>
          <div>
            <div class="card-title">{ev.get('title', 'Space Event')}</div>
            <div class="card-desc">{ev.get('summary', ev.get('current_summary', ''))[:120]}</div>
            <div class="card-badge">{severity}</div>
          </div>
        </a>"""

    # Build live data section
    live_html = ""
    for data_key, label in lens_config.get("live_metrics", []):
        value = live_data.get(data_key) or live_data.get(data_key.replace("-", "_"))
        if value is not None:
            live_html += f'<div class="metric"><span class="metric-label">{label}</span><span class="metric-value">{value}</span></div>'

    # Build Flarient links
    flarient_links_html = ""
    for link, label in lens_config.get("flarient_links", []):
        url = f"https://flarient.com{link}" if link.startswith("/") else link
        flarient_links_html += f'<a href="{url}" class="flink">{label} →</a>'

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{lens_title} — Flarient Intelligence</title>
<meta name="description" content="{lens_desc}">
<meta name="robots" content="index, follow">
<meta property="og:title" content="{lens_title}">
<meta property="og:description" content="{lens_desc}">
<meta property="og:type" content="website">
<link rel="canonical" href="https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}/micro-publications/{lens_id}.html">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0620;color:#e8eaf2;line-height:1.6}}
.header{{background:linear-gradient(135deg,{lens_color}22,{lens_color}08);border-bottom:1px solid {lens_color}33;padding:32px 20px;text-align:center}}
.header h1{{font-size:1.8rem;font-weight:700;color:#fff;margin-bottom:8px}}
.header p{{color:rgba(255,255,255,0.5);font-size:0.9rem;max-width:600px;margin:0 auto}}
.header .icon{{font-size:3rem;margin-bottom:12px}}
.container{{max-width:720px;margin:0 auto;padding:20px 16px 60px}}
.section{{margin:24px 0}}
.section h2{{font-size:0.85rem;text-transform:uppercase;letter-spacing:0.05em;color:rgba(255,255,255,0.4);margin-bottom:12px;font-weight:600}}
.card{{display:flex;gap:12px;align-items:flex-start;padding:14px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:12px;text-decoration:none;color:inherit;margin-bottom:10px;transition:background 0.2s}}
.card:hover{{background:rgba(255,255,255,0.06)}}
.card-icon{{font-size:1.5rem;flex-shrink:0}}
.card-title{{font-size:0.95rem;font-weight:600;color:#fff;margin-bottom:4px}}
.card-desc{{font-size:0.85rem;color:rgba(255,255,255,0.5)}}
.card-badge{{display:inline-block;margin-top:4px;padding:2px 8px;background:{lens_color}22;color:{lens_color};border-radius:9999px;font-size:0.75rem;font-weight:600}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}}
.metric{{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:12px;text-align:center}}
.metric-label{{display:block;font-size:0.75rem;color:rgba(255,255,255,0.4);text-transform:uppercase;margin-bottom:4px}}
.metric-value{{display:block;font-size:1.4rem;font-weight:700;color:{lens_color}}}
.flinks{{display:flex;flex-direction:column;gap:8px}}
.flink{{color:#818cf8;text-decoration:none;font-size:0.9rem;padding:10px 14px;background:rgba(99,102,241,0.08);border-radius:8px;border:1px solid rgba(99,102,241,0.15)}}
.flink:hover{{background:rgba(99,102,241,0.15)}}
.updated{{text-align:center;font-size:0.75rem;color:rgba(255,255,255,0.3);margin-top:40px}}
.brand{{text-align:center;margin-top:20px;padding-top:20px;border-top:1px solid rgba(255,255,255,0.08)}}
.brand a{{color:#818cf8;text-decoration:none;font-weight:600}}
</style>
</head>
<body>
<div class="header">
  <div class="icon">{lens_icon}</div>
  <h1>{lens_title}</h1>
  <p>{lens_desc}</p>
</div>
<div class="container">
  {live_html and f'<div class="section"><h2>Live Data</h2><div class="metrics">{live_html}</div></div>'}
  <div class="section">
    <h2>Latest Episode</h2>
    {episodes_html or '<p style="color:rgba(255,255,255,0.4)">No episodes yet.</p>'}
  </div>
  <div class="section">
    <h2>Latest Events</h2>
    {events_html or '<p style="color:rgba(255,255,255,0.4)">No recent events.</p>'}
  </div>
  <div class="section">
    <h2>Explore on Flarient</h2>
    <div class="flinks">{flarient_links_html}</div>
  </div>
</div>
<div class="updated">Updated {today}</div>
<div class="brand">
  <p>Powered by <a href="https://flarient.com">Flarient</a> — Space Decision Intelligence</p>
</div>
</body>
</html>"""

def commit_changes():
    """Commit micro publication pages to the repo."""
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    subprocess.run(["git", "config", "user.name", "Micro Publications Bot"], env=env, check=True)
    subprocess.run(["git", "config", "user.email", "micro@flarient.com"], env=env, check=True)
    subprocess.run(["git", "add", "micro-publications/"], env=env, cwd=str(REPO_DIR), check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], env=env, cwd=str(REPO_DIR))
    if result.returncode == 0:
        log("  No changes to commit")
        return
    subprocess.run(["git", "commit", "-m", "Rebuild micro publications"], env=env, check=True, cwd=str(REPO_DIR))
    import time
    for attempt in range(3):
        try:
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
            subprocess.run(["git", "push", "origin", "HEAD:main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
            log("  Changes committed and pushed")
            return
        except subprocess.CalledProcessError:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
            else:
                subprocess.run(["git", "push", "--force-with-lease", "origin", "HEAD:main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)

def main():
    log("=== Micro Publications Builder ===")
    MICRO_DIR.mkdir(parents=True, exist_ok=True)

    lenses = [
        {
            "id": "aurora-intelligence",
            "title": "Aurora Intelligence",
            "description": "Real-time aurora forecasts, Kp index tracking, and geomagnetic storm alerts. Your lens into when and where the northern lights will be visible.",
            "color": "#22d3ee",
            "icon": "🌌",
            "match_keywords": ["aurora", "kp", "geomagnetic", "storm", "northern lights", "g3", "g4", "g5"],
            "live_metrics": [("kp_index", "Kp Index"), ("aurora_forecast", "Aurora Forecast")],
            "flarient_links": [("/aurora-forecast", "Aurora Forecast"), ("/kp-index", "Kp Index"), ("/storm", "Geomagnetic Storm Tracker"), ("/aurora-trip-planner", "Aurora Trip Planner")],
        },
        {
            "id": "solar-flare-intelligence",
            "title": "Solar Flare Intelligence",
            "description": "Live solar flare monitoring, X-ray flux data, and flare classification tracking. Stay ahead of M-class and X-class solar flares.",
            "color": "#f59e0b",
            "icon": "☀️",
            "match_keywords": ["flare", "solar", "x-ray", "x-class", "m-class", "sunspot", "coronal"],
            "live_metrics": [("flare_class", "Flare Class"), ("xray_flux", "X-ray Flux")],
            "flarient_links": [("/solar-flares", "Solar Flares"), ("/observatory", "Solar Observatory"), ("/blog", "Blog Articles"), ("/podcast", "Podcast")],
        },
        {
            "id": "asteroid-intelligence",
            "title": "Asteroid Intelligence",
            "description": "Near-Earth object tracking, close approach data, and asteroid impact monitoring. Track what's flying past Earth today.",
            "color": "#6366f1",
            "icon": "🪨",
            "match_keywords": ["asteroid", "neo", "near-earth", "meteor", "approach", "impact"],
            "live_metrics": [],
            "flarient_links": [("/near-earth-objects", "Near-Earth Objects"), ("/mission-planner", "Mission Planner"), ("/blog", "Blog"), ("/podcast", "Podcast")],
        },
        {
            "id": "human-vs-ai-forecasting",
            "title": "Human vs AI Forecasting",
            "description": "Comparing human and AI space weather forecasting accuracy. Brier scores, calibration data, and prediction market results.",
            "color": "#a855f7",
            "icon": "🤖",
            "match_keywords": ["ai", "forecast", "prediction", "brier", "human", "model", "accuracy", "market"],
            "live_metrics": [],
            "flarient_links": [("/human-vs-ai", "Human vs AI"), ("/methodology", "Methodology"), ("/forecast-league", "Forecast League"), ("/research", "Research")],
        },
    ]

    episodes = fetch_rss_episodes(20)
    events = fetch_events()
    live_data = fetch_live_data()

    log(f"  Episodes: {len(episodes)}, Events: {len(events)}, Live data: {'yes' if live_data else 'no'}")

    for lens in lenses:
        html = build_lens_page(lens, episodes, events, live_data)
        (MICRO_DIR / f"{lens['id']}.html").write_text(html)
        log(f"  Built: {lens['id']}.html")

    # Build index page
    index_html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flarient Micro Publications</title>
<style>body{font-family:system-ui;background:#0a0620;color:#e8eaf2;max-width:720px;margin:0 auto;padding:20px}
h1{margin-bottom:20px}.lens{display:block;padding:20px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:12px;text-decoration:none;color:inherit;margin:10px 0}.lens:hover{background:rgba(255,255,255,0.06)}.lens h2{color:#fff;margin-bottom:6px}.lens p{color:rgba(255,255,255,0.5);font-size:0.9rem}</style>
</head><body><h1>Flarient Micro Publications</h1>
<p style="color:rgba(255,255,255,0.5);margin-bottom:20px">Content lenses over <a href="https://flarient.com" style="color:#818cf8">Flarient</a> — dynamically updated.</p>"""
    for lens in lenses:
        index_html += f'<a class="lens" href="{lens["id"]}.html"><h2>{lens["icon"]} {lens["title"]}</h2><p>{lens["description"]}</p></a>'
    index_html += "</body></html>"
    (MICRO_DIR / "index.html").write_text(index_html)

    commit_changes()
    log("=== DONE ===")

if __name__ == "__main__":
    main()
