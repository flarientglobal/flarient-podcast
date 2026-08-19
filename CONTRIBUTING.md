# Contributing to the Flarient Podcast Pipeline

Thank you for your interest in contributing! This repository contains the automated
podcast generation pipeline for [Flarient](https://flarient.com).

## Ways to Contribute

- **Report bugs** — Open an issue with a clear description and steps to reproduce
- **Suggest topics** — Use the "Topic Suggestion" issue template
- **Report audio quality issues** — Use the "Audio Quality Report" issue template
- **Improve the pipeline** — Submit a pull request with your changes

## Pull Request Process

1. Fork the repository and create your branch from `main`
2. Make your changes with clear, descriptive commit messages
3. Test your changes locally if possible
4. Open a pull request with a clear description of what you changed and why

## Development Setup

The pipeline runs on GitHub Actions with Python 3.12. To test locally:

\`\`\`bash
pip install -r requirements.txt
export GEMINI_API_KEY="your-key"
export FLARIENT_API_URL="https://flarient.com"
python generate_podcast.py
\`\`\`

## Questions?

Visit [flarient.com/contact](https://flarient.com/contact) or open an issue.
