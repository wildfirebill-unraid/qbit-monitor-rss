# qbit-monitor-rss

[![Release](https://img.shields.io/github/v/release/wildfirebill-unraid/qbit-monitor-rss?style=for-the-badge&label=Release)](https://github.com/wildfirebill-unraid/qbit-monitor-rss/releases)
[![License](https://img.shields.io/github/license/wildfirebill-unraid/qbit-monitor-rss?style=for-the-badge)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%2Famd64%2Farm64-blue?style=for-the-badge)](https://github.com/wildfirebill-unraid/qbit-monitor-rss/pkgs/container/qbit-monitor-rss)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](requirements.txt)
[![Unraid](https://img.shields.io/badge/ready%20for-Unraid-orange?style=for-the-badge)](https://unraid.net)
[![Docker](https://img.shields.io/badge/docker-24CE46?style=for-the-badge&logo=docker&logoColor=white)](Dockerfile)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge)](https://github.com/wildfirebill-unraid/qbit-monitor-rss/pulls)

![qbit-monitor-rss social preview](https://raw.githubusercontent.com/wildfirebill-unraid/qbit-monitor-rss/main/.github/social-preview.png)

**qbit-monitor-rss** is a self-hosted, Dockerized web app for [Unraid](https://unraid.net) that centralizes **multiple qBittorrent instances** and automatically grabs new releases from **private-tracker RSS feeds** — one dashboard, one configurable torrent slot budget, zero cross-instance duplicates.

Think of it as a lightweight "tracker gatekeeper": it watches your private-tracker RSS feeds, filters already-downloaded content by **info-hash and normalized title**, and adds only genuinely new torrents to the qBittorrent instance you choose — all under a global **slot cap** (with an optional per-feed override) and automatic slot release after a seed-time requirement is met.

- **Multi-instance support** — watch any number of qBittorrent WebUI instances from a single UI (one tab each).
- **Private-tracker friendly** — parse RSS 2.0 and Atom feeds, including `enclosure` and `<link href="...">` torrent downloads.
- **No duplicates across feeds or instances** — info-hash check (already added + live client) plus normalized-title check.
- **Configurable slot cap with per-feed overrides** — a global cap (default 50) plus an optional per-feed cap; slots are consumed on add and released after `seed_hours` of seeding (default 72.5 h) or when a torrent vanishes from the client.
- **Automatic + manual scanning** — per-feed **Scan** button, a global force scan, and a scheduled scan every `rss_scan_interval_minutes`.
- **Per-feed target routing** — each feed points at the instance it should add to, with optional save path, category (autocompleted from the instance's categories), and max slots.
- **Default save folder** — torrents land in `/data/torrents` by default (configurable); feeds can override per-feed.
- **Persistent storage** — SQLite database and `config.json` live in `/data`, so your state survives restarts and updates.
- **Web GUI included** — no external frontend build; a clean, sortable, tabbed interface is served directly by the app, with a **Seed left** column showing time until a slot frees.
- **Multi-arch container** — `linux/amd64` and `linux/arm64` images published to GHCR on every release.

## Table of contents

- [Why qbit-monitor-rss?](#why-qbit-monitor-rss)
- [Quick start (Unraid / Docker)](#quick-start-unraid--docker)
- [Configuration](#configuration)
  - [Settings](#settings)
  - [qBittorrent instances](#qbittorrent-instances)
  - [RSS feeds](#rss-feeds)
- [How an item flows](#how-an-item-flows)
- [FAQ](#faq)
- [Troubleshooting](#troubleshooting)
- [REST API](#rest-api)
- [Local development](#local-development)
- [Contributing](#contributing)
- [License](#license)

## Why qbit-monitor-rss?

Managing a private-tracker seedbox is usually a juggling act: multiple qBittorrent instances, half a dozen RSS feeds, and a constant fear of exceeding your tracker's download/slot limits. qbit-monitor-rss does the bookkeeping for you:

1. It watches every configured RSS feed and recognizes which items you have already fetched — by guid, by info-hash, and by normalized title.
2. It only hands new, unique torrents to the qBittorrent instance you chose for that feed.
3. It enforces a global slot budget so you never exceed your configured limit, releasing slots only once a torrent has seeded long enough (or disappears).

Result: your private-tracker ratio grows, your slot count stays under control, and you never download the same release twice — across any number of instances.

## Quick start (Unraid / Docker)

1. On your Unraid host, clone or copy this repo (or pull the image from GHCR):

   ```sh
   docker pull ghcr.io/wildfirebill-unraid/qbit-monitor-rss:latest
   ```

2. Edit `docker-compose.yml`:
   - Map a free host port on the left, e.g. `8200:8000`.
   - Attach to the docker network your qBittorrent instances live on if you want
     to reach them by container name instead of IP:
     ```yaml
     networks:
       qbit-monitor:
         networks:
           qbitnet:
             ipv4_address: 172.18.0.20
     ```
     with `networks: { qbitnet: { external: true } }` at the bottom.
3. Run:

   ```sh
   docker compose up -d --build
   ```

4. Open `http://<unraid-ip>:8200` and add your qBittorrent instances + RSS feeds
   from the web UI.

Data (SQLite database + `config.json`) persists in `./data` (`/data` inside the
container). Set the `DATA_DIR` environment variable to relocate it.

### Unraid template

An official [Unraid Community Applications](https://unraid.net/community-apps)
template is included at [`unraid/qbit-monitor-rss.xml`](unraid/qbit-monitor-rss.xml)
— the same schema used by the Community Apps plugin, so you can drop it into
`/boot/config/plugins/dockerMan/templates-user/` for a one-click install.

## Configuration

Config is `$DATA_DIR/config.json` (created on first run with defaults) and is
also editable from the GUI — no file editing required for day-to-day use.

### Settings

| Setting | Default | Range | Meaning |
|---|---|---|---|
| `max_slots` | 50 | 50–200 | Concurrent active slots (cap on how many torrents are "using" a slot) |
| `seed_hours` | 72.5 | — | Seed time (hours) before a slot is released |
| `poll_interval_seconds` | 30 | 5–3600 | How often live torrent state is refreshed |
| `rss_scan_interval_minutes` | 15 | 1–1440 | How often RSS feeds are scanned automatically |
| `data_folder` | `/data/torrents` | — | Default save path used for feeds without a per-feed save path |

Out-of-range `max_slots` values are rejected with HTTP 422 by the API; `config.json` also clamps on load. The `data_folder` setting only affects feeds that don't set their own save path.

### qBittorrent instances

Each instance is `URL + username + password`. Use the host IP and per-instance
port, e.g. `http://192.168.0.15:8081`. The app logs in via the WebUI API
(`/api/v2/auth/login`) and re-authenticates on 401/403.

### RSS feeds

Each feed points at an RSS/Atom URL, a target instance, and optional save path,
category, and max slots. The category field is autocompleted from the target
instance's existing qBittorrent categories. If a feed has no save path, the
global `data_folder` setting is used. If a feed has no `max_slots`, it shares
the global slot budget; with a per-feed `max_slots` set, that feed can never
occupy more slots than its own cap even when the global budget is free.
Scanning is manual (per-feed **Scan** button or the global force
scan) and automatic every `rss_scan_interval_minutes`.

## How an item flows

1. **Scan** parses the feed (RSS 2.0 or Atom), extracting
   guid / title / publish date / torrent URL (enclosure or link).
2. New guids become **pending**; seen guids are skipped.
3. The queue processor downloads the `.torrent`, computes its **info-hash**,
   and checks duplicates:
   - already added anywhere (hash in DB),
   - already present in any live client,
   - normalized title already tracked.
4. If unique **and** a slot is free (both the global budget and, if set, the
   feed's per-feed cap), the torrent is uploaded to the target
   instance and the slot is marked used. If a cap is reached it stays queued.
5. Every poll, torrents with `seeding_time >= seed_hours * 3600` (or that
   vanished from the client) release their slot, letting the queue advance.

## FAQ

**Does this work with private trackers?** Yes — it only uses standard RSS 2.0 /
Atom feeds and the qBittorrent WebUI API. No tracker-specific code. Download
limits remain your responsibility; the slot cap is there to help you respect them.

**How are duplicates prevented?** Three ways, in order: the feed guid is
remembered in the database; the torrent's computed info-hash is checked against
everything already added *and* everything currently in any live client; and the
normalized title is matched against tracked items. A release fetched from two
different trackers therefore still resolves to one torrent.

**What happens when the slot cap is reached?** New items stay queued and are
retried automatically as slots free up. Nothing is silently dropped. A feed with
its own `max_slots` setting is limited by that feed's cap *and* the global cap.

**When is a slot released?** After a torrent has been seeding for `seed_hours`
(default 72.5 h), or immediately if the torrent disappears from the client. The
torrent table shows a **Seed left** column counting down until release.

**Where is my data stored?** SQLite (`state.db`) and `config.json` in `/data`
(`DATA_DIR`). Back it up or mount it wherever your appdata lives.

## Troubleshooting

- **`qBittorrent unreachable` in the logs** — verify the instance URL uses the
  host IP + per-instance port, and that the client is on the same docker network
  or reachable from the container.
- **Feed scans find nothing** — some trackers require a browser/User-Agent
  header or only serve feeds over HTTPS. Check the feed URL in a browser first.
- **Items stay queued** — the slot cap is reached, or the item is a duplicate.
  The item state column explains which.

## REST API

- `GET /` — GUI
- `GET /api/state` — instances, slots, settings
- `GET /api/torrents` — all torrents
- `GET /api/rss` — feeds + items
- `POST /api/settings` — update slots/seed/poll settings + `data_folder`
- `POST /api/instances/save` | `/test` | `/delete`
- `POST /api/feeds/save` | `/delete` | `/toggle` | `/{id}/scan`
- `POST /api/rss/{guid}/action` — `ignore` / `retry` / `add-now`

## Local development

```sh
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:DATA_DIR = ".\data"; .venv\Scripts\python -m uvicorn app.main:app --port 8000
```

## Contributing

Contributions are welcome! Open an issue for bugs or feature requests, or submit
a pull request. Please see the [issue templates](.github/ISSUE_TEMPLATE) for
guidance, and join the discussion for feature ideas.

## License

[MIT](LICENSE) © wildfirebill
