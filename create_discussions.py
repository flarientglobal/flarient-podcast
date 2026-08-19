#!/usr/bin/env python3
"""
Creates a GitHub Discussion for each podcast episode and adds Podcasting 2.0
<podcast:socialInteract> reference to the RSS feed, pointing to the discussion.
This gives the podcast a canonical discussion layer on GitHub — zero cost.
"""

import os, sys, json, re, subprocess
from pathlib import Path
import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))
RSS_PATH = REPO_DIR / "podcast.xml"

def log(msg):
    print(f"[DISCUSSIONS] {msg}", flush=True)

def graphql(query, variables=None):
    """Execute a GitHub GraphQL mutation/query."""
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"}
    resp = requests.post(
        "https://api.github.com/graphql",
        headers=headers,
        json={"query": query, "variables": variables or {}},
    )
    data = resp.json()
    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})

def get_repo_node_id():
    """Get the GraphQL node ID of the repository."""
    owner, repo = REPO.split("/")
    data = graphql(f'query {{ repository(owner: "{owner}", name: "{repo}") {{ id }} }}')
    return data["repository"]["id"]

def get_discussion_category_id(repo_id, category_name="General"):
    """Get the node ID of a discussion category."""
    owner, repo = REPO.split("/")
    data = graphql(f'''query {{
      repository(owner: "{owner}", name: "{repo}") {{
        discussionCategories(first: 20) {{
          nodes {{ id name }}
        }}
      }}
    }}''')
    categories = data["repository"]["discussionCategories"]["nodes"]
    for cat in categories:
        if cat["name"].lower() == category_name.lower():
            return cat["id"]
    # Fallback: use first category
    if categories:
        log(f"  Category '{category_name}' not found, using '{categories[0]['name']}'")
        return categories[0]["id"]
    return None

def create_discussion(repo_id, category_id, title, body):
    """Create a GitHub Discussion and return its URL."""
    mutation = '''mutation CreateDiscussion($input: CreateDiscussionInput!) {
      createDiscussion(input: $input) {
        discussion { id url number }
      }
    }'''
    variables = {
        "input": {
            "repositoryId": repo_id,
            "categoryId": category_id,
            "title": title,
            "body": body,
        }
    }
    data = graphql(mutation, variables)
    return data["createDiscussion"]["discussion"]

def get_latest_episode_from_rss():
    """Parse the RSS feed to get the latest episode's GUID and title."""
    if not RSS_PATH.exists():
        return None, None
    xml = RSS_PATH.read_text(encoding="utf-8")
    # Find the first <item>
    item_match = re.search(r'<item>(.*?)</item>', xml, re.DOTALL)
    if not item_match:
        return None, None
    item = item_match.group(1)
    guid_match = re.search(r'<guid[^>]*>(.*?)</guid>', item)
    title_match = re.search(r'<title>(.*?)</title>', item)
    guid = guid_match.group(1) if guid_match else None
    title = title_match.group(1) if title_match else None
    return guid, title

def discussion_exists_for_guid(guid):
    """Check if a discussion already exists for this episode GUID."""
    owner, repo = REPO.split("/")
    # Search for discussions with the GUID in the body
    query = f'''query {{
      search(query: "repo:{owner}/{repo} type:discussions \"{guid}\"", first: 1) {{
        discussionCount
      }}
    }}'''
    try:
        data = graphql(query)
        return data.get("search", {}).get("discussionCount", 0) > 0
    except:
        return False

def update_rss_with_social_interact(guid, discussion_url):
    """Add <podcast:socialInteract> to the RSS feed item matching the GUID."""
    if not RSS_PATH.exists():
        log("  podcast.xml not found")
        return
    xml = RSS_PATH.read_text(encoding="utf-8")

    # Add podcast namespace if not present
    if "xmlns:podcast" not in xml:
        xml = xml.replace(
            '<rss version="2.0"',
            '<rss version="2.0" xmlns:podcast="https://podcastindex.org/ns/1.0"',
        )

    # Check if socialInteract already exists for this GUID
    if f"socialInteract" in xml and guid in xml:
        # Update existing socialInteract
        pattern = re.compile(
            r'(<item>.*?' + re.escape(guid) + r'.*?)(<podcast:socialInteract[^/]*/>)(.*?</item>)',
            re.DOTALL
        )
        if pattern.search(xml):
            xml = pattern.sub(
                lambda m: m.group(1) + f'<podcast:socialInteract uri="{discussion_url}" priority="1" platform="github"/>' + m.group(3),
                xml
            )
        else:
            # Add socialInteract before </item>
            item_pattern = re.compile(
                r'(<item>.*?' + re.escape(guid) + r'.*?)(</item>)',
                re.DOTALL
            )
            xml = item_pattern.sub(
                lambda m: m.group(1) + f'       <podcast:socialInteract uri="{discussion_url}" priority="1" platform="github"/>\n     ' + m.group(2),
                xml
            )
    else:
        # Add socialInteract before </item>
        item_pattern = re.compile(
            r'(<item>.*?' + re.escape(guid) + r'.*?)(</item>)',
            re.DOTALL
        )
        xml = item_pattern.sub(
            lambda m: m.group(1) + f'       <podcast:socialInteract uri="{discussion_url}" priority="1" platform="github"/>\n     ' + m.group(2),
            xml
        )

    RSS_PATH.write_text(xml, encoding="utf-8")
    log(f"  RSS feed updated with socialInteract: {discussion_url}")

def commit_rss():
    """Commit the updated RSS feed."""
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    subprocess.run(["git", "config", "user.name", "Podcast Discussions Bot"], env=env, check=True)
    subprocess.run(["git", "config", "user.email", "discussions@flarient.com"], env=env, check=True)
    subprocess.run(["git", "add", "podcast.xml"], env=env, cwd=str(REPO_DIR), check=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], env=env, cwd=str(REPO_DIR))
    if result.returncode == 0:
        log("  No RSS changes to commit")
        return
    subprocess.run(["git", "commit", "-m", "Add socialInteract to latest episode RSS"], env=env, check=True, cwd=str(REPO_DIR))
    import time
    for attempt in range(3):
        try:
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
            subprocess.run(["git", "push", "origin", "HEAD:main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)
            log("  RSS committed and pushed")
            return
        except subprocess.CalledProcessError as e:
            log(f"  Push attempt {attempt+1}/3 failed")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
            else:
                subprocess.run(["git", "push", "--force-with-lease", "origin", "HEAD:main"], env=env, check=True, cwd=str(REPO_DIR), capture_output=True, text=True)

def main():
    log("=== Podcast Discussion Creator ===")
    if not GH_TOKEN:
        log("ERROR: GITHUB_TOKEN not set")
        sys.exit(1)

    guid, title = get_latest_episode_from_rss()
    if not guid:
        log("No episodes found in RSS feed — skipping")
        sys.exit(0)

    log(f"Latest episode: {title} (GUID: {guid})")

    # Check if discussion already exists
    if discussion_exists_for_guid(guid):
        log("Discussion already exists for this episode — skipping")
        sys.exit(0)

    # Get repo and category IDs
    repo_id = get_repo_node_id()
    category_id = get_discussion_category_id(repo_id, "Episodes")
    if not category_id:
        log("No discussion categories found — please enable Discussions in repo settings")
        sys.exit(1)

    # Create discussion
    discussion_body = f"""## Episode Discussion

This is the official discussion thread for **{title}**.

Share your thoughts, questions, and observations about this episode. What did you find most interesting? What would you like to hear more about in future episodes?

---

🎧 [Listen on Flarient](https://flarient.com/podcast)
📊 [Live space weather data](https://flarient.com)
📡 [RSS Feed](https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}/podcast.xml)

*This discussion was automatically created. It is the canonical social interaction layer for this episode (Podcasting 2.0 socialInteract).*
"""

    discussion = create_discussion(repo_id, category_id, f"🎧 {title}", discussion_body)
    discussion_url = discussion["url"]
    log(f"  Discussion created: {discussion_url}")

    # Update RSS feed with socialInteract
    update_rss_with_social_interact(guid, discussion_url)

    # Commit RSS changes
    commit_rss()

    log("=== DONE ===")

if __name__ == "__main__":
    main()
