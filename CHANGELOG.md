# Changelog

All notable changes to qbit-monitor-rss are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [v0.8.0] - 2026-08-02

### Added
- **Tracker URL + abbreviation** — trackers now carry an announce `url` and an optional `abbr` (short name). The abbreviation is shown throughout the UI (torrents table, tracker selects, feed cards) and is used to identify the tracker, falling back to the URL when no abbreviation is set.

### Changed
- **Automatic tracker matching** — qbit-monitor-rss now watches each client's per-torrent `tracker` URL and automatically tracks torrents against a configured tracker whose `abbr` (or `url`) matches. Already-seeded torrents added outside the app now correctly occupy (and later release) slot budget entries.

## [v0.7.0-beta] - 2026-08-02

### Added
- **Per-tracker slot budgets** — `max_slots` and `seed_hours` moved from the global/feed level to each tracker. Every feed pointing at the same tracker shares that tracker's slot budget.
- **Tracker management UI + API** — add/edit/delete trackers, set `max_slots`, `seed_hours`, and a `public` flag. Public trackers have no slot or seed limits and never occupy a slot.
- **Manual adds can join a tracker's budget** — magnet / `.torrent` URL / file uploads can be assigned a tracker and count toward that tracker's slots, just like RSS adds.
- **Multi-file `.torrent` upload** — the **+ Add torrent** dialog uploads one or more `.torrent` files at once.
- New app icon (`static/icon.png`).

### Changed
- Slot settings removed from global config and feeds — configure them per tracker. Feeds with no tracker fall back to built-in defaults (50 slots / 72.5 h, non-public).
- The global slot bar and per-torrent **Seed left** countdown were removed; the table now shows each torrent's accumulated **Seed time**.

## [v0.6.1-beta] - 2026-07-31

### Fixed
- **Container startup crash** — the app now installs `python-multipart` so multipart form/file uploads work.

## [v0.6.0-beta] - 2026-07-31

### Added
- **Separate add-magnet and add-torrent (local file upload) dialogs** — upload one or more `.torrent` files from your machine, with optional save path / category.

## [v0.5.0-beta] - 2026-07-31

### Added
- **Add torrents from the GUI** — magnet links / `.torrent` URLs (one per line, optional save path/category).
- **Move torrents between instances** — files stay on disk, the destination re-adds at the same save path and rechecks; seed time carried over.

### Changed
- README updated for manual add/move.
- Added a TODO section.

## [v0.4.0-beta] - 2026-07-30

### Added
- **Update checker** on the About page.

### Changed
- Release tag baked into `APP_VERSION` at image build time.

## [v0.3.0-beta] - 2026-07-29

### Added
- **About tab** with app info, version, and links.
- Web GUI screenshots in the README.
- Documentation for commercial seedbox support and non-Unraid Docker usage.

## [v0.2.0-beta] - 2026-07-28

### Added
- Per-feed max slots, default save folder, per-instance categories, GUI updates.
- Unraid template updates (WebUI port config field; later simplified).

## [v0.1.0-beta] - 2026-07-27

### Added
- Initial release: multi-instance qBittorrent monitoring, private-tracker RSS auto-add, deduplication by info-hash and normalized title, slot budgets, web GUI, Dockerized Unraid support, README, license, issue templates, Unraid template, social preview.

[Unreleased]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.8.0...HEAD
[v0.8.0]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.7.0-beta...v0.8.0
[v0.7.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.6.1-beta...v0.7.0-beta
[v0.6.1-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.6.0-beta...v0.6.1-beta
[v0.6.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.5.0-beta...v0.6.0-beta
[v0.5.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.4.0-beta...v0.5.0-beta
[v0.4.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.3.0-beta...v0.4.0-beta
[v0.3.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.2.0-beta...v0.3.0-beta
[v0.2.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/compare/v0.1.0-beta...v0.2.0-beta
[v0.1.0-beta]: https://github.com/wildfirebill-unraid/qbit-monitor-rss/releases/tag/v0.1.0-beta
