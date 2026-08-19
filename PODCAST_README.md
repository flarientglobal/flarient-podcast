# Daily Space Podcast by Flarient

This repo contains the automated podcast generation pipeline for [Flarient](https://flarient.com).

## Episode Types

- **Daily** (Mon-Sat, 30 min) — Standard episode covering live space weather, blog articles, events, fact checks, and daily brief
- **Weekly Roundup** (Sunday, 45 min) — Top 5 space weather events of the week with a "week ahead" segment
- **Breaking Special** (anytime, 30 min) — Triggered automatically when a G4+ geomagnetic storm or X-class solar flare is detected. Leads with the breaking event, then covers regular content.

**One episode per day max** — if a breaking special runs first, the daily episode is skipped.

## How It Works

1. **Content Fetch** — Pulls live space weather data, blog posts, events, fact checks, daily briefs, and "This Day in History" from the Flarient API
2. **Deduplication** — Tracks covered content IDs in `covered_content.json` to prevent repeating the same articles/events/fact checks across episodes
3. **Script Generation** — Gemini AI generates a conversational script with two consistent hosts (Christopher & Jenny)
4. **Text-to-Speech** — edge-tts (Microsoft Azure neural voices) synthesizes speech — zero cost, no API key
5. **Audio Mastering** — ffmpeg: silence removal, background music bed, ID3 chapter markers, loudnorm to -16 LUFS (±2 LUFS tolerance)
6. **Cover Art** — Per-episode 3000×3000 JPG generated with Pillow (date, Kp index, episode type badge)
7. **Transcript** — Full dialogue saved as .txt
8. **Publishing** — MP3, cover art, and transcript uploaded as GitHub Release assets (permanent public URLs)
9. **RSS Feed** — podcast.xml updated and committed to the repo, hosted via GitHub Pages

## Consistent Voices

- **Christopher** (en-US-ChristopherNeural) — Male host, the space weather expert
- **Jenny** (en-US-JennyNeural) — Female host, the curious co-host

These Microsoft Azure neural voices are consistently rated as the most natural-sounding free TTS voices and are used in every episode for consistency.

## Cost

**Free** — all components use free tiers:
- Gemini API (free tier)
- edge-tts (free, no API key)
- GitHub Actions (free for public repos, 2000 min/month for private)
- GitHub Releases (free, 2GB storage)
- GitHub Pages (free)
- Pillow (open source, runs on GitHub runner)

## Setup

The pipeline is managed from the [Flarient Podcast Setup page](https://flarient.com/podcast-setup).

## RSS Feed

After enabling GitHub Pages (Settings → Pages → Source: main branch /root), the RSS feed is available at:

\`\`\`
https://<your-username>.github.io/<repo-name>/podcast.xml
\`\`\`

Submit this URL to:
- [Spotify for Podcasters](https://podcasters.spotify.com)
- [Apple Podcasts Connect](https://podcastsconnect.apple.com)
- [Amazon Music](https://podcasters.amazon.com)
- [YouTube Studio](https://studio.youtube.com) (RSS podcast ingestion)
- [Pocket Casts](https://pocketcasts.com)

## Custom Jingles

To use your own intro/outro music, place files at:
- `assets/intro.mp3` — intro jingle (3-5 seconds)
- `assets/outro.mp3` — outro jingle (3-5 seconds)

If these files don't exist, simple generated jingles are used as fallback.

## Deduplication

The script maintains `covered_content.json` in the repo, tracking the last 200 content IDs (articles, events, fact checks) that have been covered. This prevents the same content from being discussed in consecutive episodes.

## Schedule

- **Mon-Sat 06:30 UTC** — Daily episode (30 min)
- **Sunday 06:30 UTC** — Weekly roundup (45 min)
- **Breaking events** — Triggered automatically via `repository_dispatch` when a G4+ storm or X-class flare is detected

You can also trigger it manually from the Actions tab or the Flarient setup page.
