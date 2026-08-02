# qbit-monitor-rss

A Dockerized web app for Unraid that watches **multiple qBittorrent instances**
and auto-adds new items from **private-tracker RSS feeds** into a chosen
instance, capped at a configurable **50–200 slot limit**.

- All torrents shown in tabs: **All** + one tab per instance.
- RSS items are added to the instance configured on the feed, **never
  duplicated** across feeds/instances (info-hash + normalized-title checks).
- A slot is consumed when a torrent is added and released once it has seeded
  for `seed_hours` (default **72.5 h**) — or immediately if the torrent
  disappears from the client.

## Quick start (Unraid)

1. Copy this folder (or the image build) onto your Unraid host.
2. Edit `docker-compose.yml`:
   - Map a free host port on the left, e.g. `8200:8000`.
   - Attach to the docker network your qBittorrent instances live on if you
     want to reach them by container name instead of IP:
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
4. Open `http://<unraid-ip>:8200`.

Data (SQLite + `config.json`) persists in `./data` (`/data` in the container).

## Configuring

Config is `$DATA_DIR/config.json` (created on first run with defaults) and is
also editable from the GUI:

| Setting | Default | Range | Meaning |
|---|---|---|---|
| `max_slots` | 50 | 50–200 | Concurrent active slots |
| `seed_hours` | 72.5 | — | Seed time before a slot is released |
| `poll_interval_seconds` | 30 | 5–3600 | How often torrents are refreshed |
| `rss_scan_interval_minutes` | 15 | 1–1440 | How often feeds are scanned |

Out-of-range `max_slots` values are rejected with HTTP 422; `config.json` also
clamps on load.

### qBittorrent instances

Each instance is `URL + username + password`. Use the host IP and per-instance
port, e.g. `http://192.168.0.15:8081`. The app logs in via the WebUI API
(`/api/v2/auth/login`) and re-authenticates on 401/403.

### RSS feeds

Each feed points at an RSS/Atom URL, a target instance, and optional save path
+ category. Scan is manual (per-feed **Scan** button or the global force scan)
and automatic every `rss_scan_interval_minutes`.

## How an item flows

1. **Scan** parses the feed (RSS 2.0 or Atom), extracting
   guid / title / publish date / torrent URL (enclosure or link).
2. New guids become **pending**; seen guids are skipped.
3. The queue processor downloads the `.torrent`, computes its **info-hash**,
   and checks duplicates:
   - already added anywhere (hash in DB),
   - already present in any live client,
   - normalized title already tracked.
4. If unique **and** a slot is free, the torrent is uploaded to the target
   instance and the slot is marked used. If the cap is reached it stays queued.
5. Every poll, torrents with `seeding_time >= seed_hours * 3600` (or that
   vanished from the client) release their slot, letting the queue advance.

## Endpoints

- `GET /` — GUI
- `GET /api/state` — instances, slots, settings
- `GET /api/torrents` — all torrents
- `GET /api/rss` — feeds + items
- `POST /api/settings` — update slots/seed/poll settings
- `POST /api/instances/save` | `/test` | `/delete`
- `POST /api/feeds/save` | `/delete` | `/toggle` | `/{id}/scan`
- `POST /api/rss/{guid}/action` — `ignore` / `retry` / `add-now`

## Local development

```sh
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:DATA_DIR = ".\data"; .venv\Scripts\python -m uvicorn app.main:app --port 8000
```
