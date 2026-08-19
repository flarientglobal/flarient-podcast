# Flarient Micro Publications

Content lenses over [Flarient](https://flarient.com) — dynamically generated pages that
surface the latest relevant content for each domain.

## Lenses

- **[Aurora Intelligence](https://flarientglobal.github.io/flarient-podcast/micro-publications/aurora-intelligence.html)** — Real-time aurora forecasts, Kp index, geomagnetic storm alerts
- **[Solar Flare Intelligence](https://flarientglobal.github.io/flarient-podcast/micro-publications/solar-flare-intelligence.html)** — Live solar flare monitoring, X-ray flux, flare classification
- **[Asteroid Intelligence](https://flarientglobal.github.io/flarient-podcast/micro-publications/asteroid-intelligence.html)** — Near-Earth object tracking, close approach data
- **[Human vs AI Forecasting](https://flarientglobal.github.io/flarient-podcast/micro-publications/human-vs-ai-forecasting.html)** — Brier scores, calibration, prediction market results

## How It Works

Every 6 hours, a GitHub Actions workflow:
1. Fetches the latest podcast episodes from the RSS feed
2. Fetches recent space events from the Flarient API
3. Fetches live space weather data from the Flarient API
4. Filters content by domain-specific keywords
5. Builds lightweight HTML pages with the latest content
6. Commits and deploys via GitHub Pages

All links point back to relevant Flarient.com URLs.

## Cost

Free — GitHub Pages + GitHub Actions + Flarient API.
