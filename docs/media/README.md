# Project media

This directory is the canonical home for screenshots and screen recordings used in the public README, release notes, and project overview.

## Recommended capture names

- `overview.png` — overview dashboard and portfolio health.
- `linked-accounts.png` — source connection and sync controls.
- `activity.png` — event review, issue resolution, and pagination.
- `reports.png` — readiness and export workflows.
- `settings-security.png` — settings, secret inventory, backup, and tax integration controls.
- `project-tour.webm` or `project-tour.mp4` — short end-to-end product tour.
- `desktop-overview.png`, `desktop-linked-accounts.png`, `desktop-activity.png`, `desktop-reports.png`, `desktop-settings-security.png` — desktop layout captures.
- `desktop-tour.gif` — short desktop product tour.

Use relative links from the root README, for example:

```markdown
![Activity review](docs/media/activity.png)
```

## Before committing media

- Use demo or synthetic data only.
- Redact names, email addresses, wallet addresses, transaction hashes, API keys, tokens, backup filenames, local filesystem paths, and browser profile information.
- Check both the visible frame and any recorded audio for private information.
- Prefer compressed, web-friendly formats and short recordings.
- Keep the README links synchronized with the files that actually exist in this directory.

The current captures were taken from the local demo workspace and are linked from the root README. Add future approved captures here and link only files that have been visually checked.
