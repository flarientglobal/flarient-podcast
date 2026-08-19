#!/usr/bin/env python3
"""
Evergreen Reactivation Engine — detects current space weather events and matches them
to old podcast episodes via their genome topics. Old episodes become new again when
relevant events occur. Creates reactivation entries for promotion.
Zero cost: Flarient API + GitHub Actions.
"""

import os, sys, json, re, subprocess, datetime
from pathlib import Path
import requests

FLARIENT_API = os.environ.get("FLARIENT_API_URL", "https://flarient.com").rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))
GENOMES_PATH = REPO_DIR / "episode_genomes.json"
REACTIVATION_PATH = REPO_DIR / "reactivation_queue.json"

def log(msg):
    print(f"[REACTIVATION] {msg}", flush=True)

def fetch_live_data():
    """Fetch current space weather from Flarient API."""
    try:
        resp = requests.get(f"{FLARIENT_API}/api/functions/getLiveSpaceWeather", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"  Failed to fetch live data: {e}")
        return {}

def fetch_events():
    """Fetch recent space events from Flarient."""
    try:
        resp = requests.get(f"{FLARIENT_API}/api/functions/getPodcastContent", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", []) or []
    except Exception as e:
        log(f"  Failed to fetch events: {e}")
        return []

def detect_active_triggers(live_data, events):
    """Detect significant space weather events that should trigger reactivation."""
    triggers = []

    # Check live data
    kp = live_data.get("kp_index") or live_data.get("kp")
    if kp is not None:
        try:
            kp_val = float(kp)
            if kp_val >= 7:
                triggers.append({"topic": "kp_7_plus", "description": f"Kp index at {kp_val} (G3+ storm)", "severity": "high"})
            elif kp_val >= 6:
                triggers.append({"topic": "g3_storm", "description": f"Kp index at {kp_val} (G3 storm)", "severity": "moderate"})
        except (ValueError, TypeError):
            pass

    flare = live_data.get("flare_class") or live_data.get("xray_class")
    if flare:
        flare_upper = str(flare).upper()
        if flare_upper.startswith("X"):
            triggers.append({"topic": "x_class_flare", "description": f"X-class solar flare detected ({flare_upper})", "severity": "extreme"})
        elif flare_upper.startswith("M"):
            triggers.append({"topic": "solar_flare", "description": f"M-class solar flare detected ({flare_upper})", "severity": "moderate"})

    speed = live_data.get("solar_wind_speed") or live_data.get("speed")
    if speed is not None:
        try:
            speed_val = float(speed)
            if speed_val >= 800:
                triggers.append({"topic": "record_solar_wind", "description": f"High solar wind speed: {speed_val} km/s", "severity": "high"})
        except (ValueError, TypeError):
            pass

    # Check events
    for ev in events[:10]:
        ev_type = (ev.get("event_type") or "").lower()
        ev_title = (ev.get("title") or "").lower()
        severity = ev.get("severity", "").lower()

        if "geomagnetic_storm" in ev_type or "geomagnetic" in ev_title:
            if any(g in severity for g in ["g3", "g4", "g5"]):
                triggers.append({"topic": "g3_storm", "description": f"Geomagnetic storm: {ev.get('title')}", "severity": "high"})
            elif "g2" in severity:
                triggers.append({"topic": "geomagnetic_storm", "description": f"Geomagnetic storm: {ev.get('title')}", "severity": "moderate"})

        if "aurora" in ev_type or "aurora" in ev_title:
            triggers.append({"topic": "major_aurora", "description": f"Aurora event: {ev.get('title')}", "severity": "moderate"})

        if "cme" in ev_type or "cme" in ev_title:
            if "earth" in ev_title or "directed" in ev_title:
                triggers.append({"topic": "earth_directed_cme", "description": f"Earth-directed CME: {ev.get('title')}", "severity": "high"})

        if "asteroid" in ev_type or "neo" in ev_type or "asteroid" in ev_title:
            triggers.append({"topic": "close_asteroid", "description": f"Close asteroid approach: {ev.get('title')}", "severity": "moderate"})

        if "flare" in ev_type or "flare" in ev_title:
            if "x-class" in severity or "x" in severity:
                triggers.append({"topic": "x_class_flare", "description": f"X-class flare: {ev.get('title')}", "severity": "extreme"})

    # Deduplicate by topic
    seen = set()
    unique = []
    for t in triggers:
        if t["topic"] not in seen:
            seen.add(t["topic"])
            unique.append(t)

    return unique

def load_genomes():
    """Load episode genome topics from JSON file."""
    if not GENOMES_PATH.exists():
        return {}
    try:
        return json.loads(GENOMES_PATH.read_text())
    except:
        return {}

def find_matching_episodes(genomes, trigger_topic):
    """Find episodes whose genome topics match the trigger."""
    matching = []
    for episode_date, topics in genomes.items():
        if trigger_topic in topics:
            matching.append(episode_date)
    return matching

def load_reactivation_queue():
    """Load the existing reactivation queue."""
    if not REACTIVATION_PATH.exists():
        return {"queue": [], "last_run": None}
    try:
        return json.loads(REACTIVATION_PATH.read_text())
    except:
        return {"queue": [], "last_run": None}

def update_reactivation_queue(triggers, genomes):
    """Update the reactivation queue with new matches."""
    queue_data = load_reactivation_queue()
    existing_keys = {item.get("key") for item in queue_data.get("queue", [])}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    new_entries = []
    for trigger in triggers:
        matching_episodes = find_matching_episodes(genomes, trigger["topic"])
        for ep_date in matching_episodes:
            key = f"{trigger['topic']}-{ep_date}-{now[:10]}"
            if key not in existing_keys:
                new_entries.append({
                    "key": key,
                    "trigger_topic": trigger["topic"],
                    "trigger_description": trigger["description"],
                    "severity": trigger["severity"],
                    "episode_date": ep_date,
                    "created_at": now,
                    "promoted": False,
                })

    queue_data["queue"] = (queue_data.get("queue", []) + new_entries)[-100:]  # Keep last 100
    queue_data["last_run"] = now

    REACTIVATION_PATH.write_text(json.dumps(queue_data, indent=2))
    log(f"  Reactivation queue: {len(new_entries)} new entries ({len(queue_data['queue'])} total)")
    return new_entries

def commit_changes():
    """Commit reactivation queue to the repo."""
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    subprocess.run(["git", "config", "user.name", "Reactivation Bot"], env=env, check=True)
    subprocess.run(["git", "config", "user.email", "reactivation@flarient.com"], env=env, check=True)
    subprocess.run(["git", "add", "reactivation_queue.json"], env=env, cwd=str(REPO_DIR), check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], env=env, cwd=str(REPO_DIR))
    if result.returncode == 0:
        log("  No changes to commit")
        return
    subprocess.run(["git", "commit", "-m", "Evergreen reactivation: scan for event matches"], env=env, check=True, cwd=str(REPO_DIR))
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
    log("=== Evergreen Reactivation Engine ===")

    live_data = fetch_live_data()
    events = fetch_events()
    triggers = detect_active_triggers(live_data, events)

    log(f"  Active triggers: {len(triggers)}")
    for t in triggers:
        log(f"    - {t['topic']}: {t['description']} (severity: {t['severity']})")

    if not triggers:
        log("  No significant triggers detected — skipping")
        sys.exit(0)

    genomes = load_genomes()
    log(f"  Episode genomes: {len(genomes)} episodes")

    if not genomes:
        log("  No episode genomes found — skipping")
        sys.exit(0)

    new_entries = update_reactivation_queue(triggers, genomes)

    if new_entries:
        commit_changes()
        log(f"  Reactivated {len(new_entries)} old episodes")
    else:
        log("  No new reactivation matches (already queued)")

    log("=== DONE ===")

if __name__ == "__main__":
    main()
