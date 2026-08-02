"""Background manager: polls qBittorrent instances, scans RSS feeds, manages
the limited-slot auto-download queue and cross-instance de-duplication.
"""

import asyncio
import logging
import time
from urllib.parse import quote

from . import rss as rssmod
from .bencode import hex_info_hash
from .db import DB
from .qbit import QBittorrent

log = logging.getLogger("manager")


class Manager:
    def __init__(self, config: dict, db: DB):
        self.config = config
        self.db = db
        self.clients: dict[int, QBittorrent] = {}
        self.instance_cache: dict[int, dict] = {}
        self._tasks: list[asyncio.Task] = []
        self._scan_lock = asyncio.Lock()
        self._scanning = set()
        self._started = False

    # ------------------------------------------------------------------ setup
    def reload(self):
        """Re-build clients from the current config."""
        seen = set()
        for inst in self.config.get("instances", []):
            iid = inst["id"]
            seen.add(iid)
            if iid not in self.clients:
                self.clients[iid] = QBittorrent(inst)
            else:
                self.clients[iid].username = inst.get("username", "")
                self.clients[iid].password = inst.get("password", "")
                self.clients[iid].url = inst.get("url", "").rstrip("/")
            self.instance_cache.setdefault(iid, {"torrents": [], "categories": [], "connected": False, "error": "", "version": None})
        # drop removed instances
        for iid in list(self.clients):
            if iid not in seen:
                self.clients.pop(iid).close_safe()
                self.instance_cache.pop(iid, None)

    async def start(self):
        if self._started:
            return
        self._started = True
        self.reload()
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        self._tasks.append(asyncio.create_task(self._rss_loop()))

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for c in self.clients.values():
            await c.close()

    # -------------------------------------------------------------- poll loop
    async def _poll_loop(self):
        while True:
            try:
                await self._poll_once()
                await self._process_queue()
            except Exception:
                log.exception("poll loop error")
            await asyncio.sleep(self.config.get("poll_interval_seconds", 30))

    async def _poll_once(self):
        now = time.time()
        for inst in self.config.get("instances", []):
            iid = inst["id"]
            if iid not in self.clients:
                continue
            client = self.clients[iid]
            cache = self.instance_cache.setdefault(iid, {"torrents": [], "categories": [], "connected": False, "error": "", "version": None})
            try:
                ver = await client.version()
                if ver is None:
                    cache["connected"] = False
                    cache["error"] = "unreachable or bad credentials"
                    continue
                cache["version"] = ver
                torrents = await client.torrents()
                cache["torrents"] = torrents
                cache["categories"] = await client.categories()
                cache["connected"] = True
                cache["error"] = ""
                cache["last_fetch"] = now
            except Exception as exc:  # noqa: BLE001
                cache["connected"] = False
                cache["error"] = str(exc)
        self._update_tracked()

    def _tracker_for_feed(self, feed: dict):
        """Resolve the tracker config a feed belongs to, or None when unassigned."""
        if not feed:
            return None
        tid = feed.get("tracker_id")
        if tid is None:
            return None
        return self._tracker_by_id(tid)

    def _tracker_by_id(self, tid):
        return next(
            (tr for tr in self.config.get("trackers", []) if tr["id"] == tid), None
        )

    def _tracker_seed_hours(self, tracker: dict) -> float:
        val = tracker.get("seed_hours") if tracker else None
        if val:
            return float(val)
        return 72.5

    def _tracker_slot_limit(self, tracker: dict) -> int:
        val = tracker.get("max_slots") if tracker else None
        if val:
            return int(val)
        return 50

    def _feeds_for_tracker(self, tracker: dict) -> list[int]:
        """All feed ids sharing a tracker's slot budget."""
        if not tracker:
            return []
        return [
            f["id"]
            for f in self.config.get("feeds", [])
            if f.get("tracker_id") == tracker["id"]
        ]

    def _update_tracked(self):
        """Release slots for torrents that have seeded long enough or vanished."""
        feeds_by_id = {f["id"]: f for f in self.config.get("feeds", [])}
        now = time.time()
        for t in self.db.tracked_all():
            if t["slot_released"]:
                continue
            feed = feeds_by_id.get(t["feed_id"]) if t["feed_id"] is not None else None
            tracker = self._tracker_for_feed(feed) or (
                self._tracker_by_id(t["tracker_id"]) if t["tracker_id"] is not None else None
            )
            if tracker and tracker.get("public"):
                # public-tracker torrents never occupy a slot
                self.db.update_tracked(t["hash"], t["instance_id"], slot_released=1)
                continue
            threshold = self._tracker_seed_hours(tracker) * 3600.0
            cache = self.instance_cache.get(t["instance_id"])
            found = None
            if cache:
                h = (t["hash"] or "").lower()
                found = next(
                    (x for x in cache["torrents"] if (x.get("hash") or "").lower() == h),
                    None,
                )
            if found is None:
                # torrent no longer in the client -> slot is free
                self.db.update_tracked(t["hash"], t["instance_id"], slot_released=1)
                continue
            seed_sec = found.get("seeding_time", 0) or 0
            self.db.update_tracked(
                t["hash"], t["instance_id"], last_seed_seconds=seed_sec, last_seen_at=now
            )
            if seed_sec >= threshold:
                log.info("Slot released: %s seeded %.1fh", t["title"], seed_sec / 3600)
                self.db.update_tracked(t["hash"], t["instance_id"], slot_released=1)

    # ------------------------------------------------------------ RSS scanner
    async def _rss_loop(self):
        while True:
            try:
                await self.scan_all_feeds()
            except Exception:
                log.exception("rss loop error")
            await asyncio.sleep(self.config.get("rss_scan_interval_minutes", 15) * 60)

    async def scan_all_feeds(self) -> dict:
        results = {}
        async with self._scan_lock:
            for feed in self.config.get("feeds", []):
                if not feed.get("enabled", True):
                    continue
                fid = feed["id"]
                if fid in self._scanning:
                    continue
                self._scanning.add(fid)
                try:
                    results[fid] = await self.scan_feed(feed)
                finally:
                    self._scanning.discard(fid)
        return results

    async def scan_feed(self, feed: dict) -> dict:
        feed_id = feed["id"]
        text = await rssmod.fetch_feed(feed["url"])
        if text is None:
            return {"ok": False, "error": "could not fetch feed"}
        items = rssmod.parse_feed(text)
        new_count = 0
        dup_count = 0
        for item in items:
            if self.db.item_exists(item["guid"]):
                continue
            norm = rssmod.normalize_title(item["title"])
            if self._title_exists(norm):
                self.db.insert_item(
                    item["guid"], feed_id, item["title"], item["link"],
                    item.get("pub_date", ""), feed["instance_id"], state="duplicate",
                    torrent_url=item.get("torrent_url"),
                )
                self.db.update_item(item["guid"], error="already present in an instance")
                dup_count += 1
                continue
            self.db.insert_item(
                item["guid"], feed_id, item["title"], item["link"],
                item.get("pub_date", ""), feed["instance_id"], state="pending",
                torrent_url=item.get("torrent_url"),
            )
            new_count += 1
        feed["last_scan"] = time.time()
        return {"ok": True, "new": new_count, "duplicates": dup_count}

    def _title_exists(self, norm: str) -> bool:
        for cache in self.instance_cache.values():
            for t in cache.get("torrents", []):
                if rssmod.normalize_title(t.get("name", "")) == norm:
                    return True
        for row in self.db.tracked_all():
            if rssmod.normalize_title(row.get("title", "")) == norm:
                return True
        return False

    # ------------------------------------------------------------ add queue
    async def _process_queue(self):
        feeds_by_id = {f["id"]: f for f in self.config.get("feeds", [])}
        enabled_ids = [
            f["id"] for f in self.config.get("feeds", []) if f.get("enabled", True)
        ]
        for _ in range(200):  # safety cap
            pending = self.db.pending_items(feed_ids=enabled_ids)
            if not pending:
                return
            item = None
            for i in pending:
                feed = feeds_by_id.get(i["feed_id"], {})
                tracker = self._tracker_for_feed(feed)
                if tracker and tracker.get("public"):
                    item = i
                    break
                limit = self._tracker_slot_limit(tracker)
                budget = self._feeds_for_tracker(tracker) or [i["feed_id"]]
                if tracker:
                    used = self.db.slots_in_use_for_tracker(tracker["id"], budget)
                else:
                    used = self.db.slots_in_use_for_feeds(budget)
                if limit > used:
                    item = i
                    break
            if item is None:
                return
            result = await self._try_add(item)
            if result in ("retry",):
                return

    async def _try_add(self, item: dict) -> str:
        feed = next(
            (f for f in self.config.get("feeds", []) if f["id"] == item["feed_id"]), None
        )
        if feed is None:
            self.db.update_item(item["guid"], state="error", error="feed removed")
            return "error"
        instance = next(
            (i for i in self.config.get("instances", []) if i["id"] == feed["instance_id"]),
            None,
        )
        if instance is None:
            self.db.update_item(item["guid"], state="error", error="target instance missing")
            return "error"
        client = self.clients.get(instance["id"])
        if client is None:
            self.db.update_item(item["guid"], state="error", error="client not initialised")
            return "error"

        torrent_bytes = await rssmod.download_torrent(item["torrent_url"])
        if not torrent_bytes:
            return "retry"  # transient network problem, keep queued
        info_hash = hex_info_hash(torrent_bytes)
        if not info_hash:
            self.db.update_item(item["guid"], state="error", error="could not read .torrent file")
            return "error"
        if self.db.hash_added_anywhere(info_hash) or self._hash_live(info_hash):
            self.db.update_item(
                item["guid"], state="duplicate", info_hash=info_hash,
                error="info-hash already present",
            )
            return "duplicate"

        filename = f"{info_hash[:16]}.torrent"
        save_path = feed.get("savepath", "") or self.config.get("data_folder", "/data/torrents")
        resp = await client.add_file(
            [(filename, torrent_bytes)], save_path, feed.get("category", "")
        )
        resp_l = resp.lower()
        if "duplicate" in resp_l:
            self.db.update_item(
                item["guid"], state="duplicate", info_hash=info_hash,
                error=resp,
            )
            return "duplicate"
        if resp != "Ok.":
            if resp.startswith("error:") or resp == "auth failed":
                self.db.update_item(item["guid"], info_hash=info_hash, error=resp)
                return "retry"
            self.db.update_item(
                item["guid"], state="error", info_hash=info_hash, error=resp
            )
            return "error"

        self.db.record_added(info_hash, instance["id"], item["title"])
        tracker = self._tracker_for_feed(feed)
        self.db.track_torrent(
            info_hash, instance["id"], item["title"], feed["id"],
            slot_released=1 if (tracker and tracker.get("public")) else 0,
        )
        self.db.update_item(
            item["guid"], state="added", info_hash=info_hash, torrent_hash=info_hash,
            error=None,
        )
        log.info("Added %s -> instance %s (slot taken)", item["title"], instance["id"])
        return "added"

    def _hash_live(self, info_hash: str) -> bool:
        h = info_hash.lower()
        for cache in self.instance_cache.values():
            for t in cache.get("torrents", []):
                if (t.get("hash") or "").lower() == h:
                    return True
        return False

    # ------------------------------------------------------- manual actions
    @staticmethod
    def _btih_from_magnet(url: str) -> str | None:
        """Extract a normalized 40-char info hash from a magnet link, if any."""
        import re as _re
        m = _re.search(r"[?&]xt=urn:btih:([A-Za-z0-9]+)", url)
        if not m:
            return None
        tok = m.group(1)
        if len(tok) == 40:
            return tok.lower()
        if len(tok) == 32:  # base32 btih
            try:
                import base64
                return base64.b32decode(tok.upper()).hex().lower()
            except Exception:
                return None
        return None

    async def _track_manual(self, instance_id: int, tracker_id: int,
                            hashes: list[tuple[str, str]]) -> None:
        """Register manually added torrents against a tracker so they occupy a
        slot (unless the tracker is public)."""
        tracker = self._tracker_by_id(tracker_id)
        if tracker is None:
            return
        slot_released = 1 if tracker.get("public") else 0
        for info_hash, title in hashes:
            if not info_hash:
                continue
            self.db.record_added(info_hash, instance_id, title, source="manual")
            self.db.track_torrent(
                info_hash, instance_id, title, None,
                slot_released=slot_released, tracker_id=tracker_id,
            )
            log.info(
                "Manual %s -> instance %s tracked against tracker %s%s",
                title, instance_id, tracker.get("name", tracker_id),
                " (public, no slot)" if slot_released else " (slot taken)",
            )

    async def add_torrent(
        self, instance_id: int, urls: str, save_path: str = "", category: str = "",
        tracker_id: int | None = None,
    ) -> dict:
        """Add one or more magnet links / .torrent URLs to an instance."""
        inst = self._instance_config(instance_id)
        if inst is None:
            return {"ok": False, "error": "instance not found"}
        client = self.clients.get(instance_id)
        if client is None:
            return {"ok": False, "error": "client not initialised"}
        resp = await client.add_url(urls, save_path, category)
        if resp == "Ok.":
            if tracker_id is not None:
                hashes = []
                for line in urls.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.lower().startswith("magnet:"):
                        hashes.append((self._btih_from_magnet(line), line))
                    elif line.lower().startswith(("http://", "https://")):
                        data = await rssmod.download_torrent(line)
                        h = hex_info_hash(data) if data else None
                        hashes.append((h, line))
                await self._track_manual(instance_id, tracker_id, hashes)
            return {"ok": True, "resp": resp}
        if resp.startswith("error:") or resp == "auth failed":
            return {"ok": False, "error": resp}
        return {"ok": True, "resp": resp}

    async def add_torrent_file(
        self, instance_id: int, files: list[tuple[str, bytes]],
        save_path: str = "", category: str = "", tracker_id: int | None = None,
    ) -> dict:
        """Upload one or more local .torrent files to an instance."""
        inst = self._instance_config(instance_id)
        if inst is None:
            return {"ok": False, "error": "instance not found"}
        client = self.clients.get(instance_id)
        if client is None:
            return {"ok": False, "error": "client not initialised"}
        resp = await client.add_file(files, save_path, category)
        if resp == "Ok.":
            if tracker_id is not None:
                hashes = []
                for filename, payload in files:
                    h = hex_info_hash(payload)
                    if h:
                        hashes.append((h, filename))
                await self._track_manual(instance_id, tracker_id, hashes)
            return {"ok": True, "resp": resp}
        if resp.startswith("error:") or resp == "auth failed":
            return {"ok": False, "error": resp}
        return {"ok": True, "resp": resp}

    def _instance_config(self, iid: int):
        return next((i for i in self.config.get("instances", []) if i["id"] == iid), None)

    async def move_torrents(self, from_id: int, to_id: int, hashes: list[str]) -> dict:
        """Re-home torrents: add to the destination (same save path) then drop
        from the source without deleting the data on disk."""
        if from_id == to_id:
            return {"ok": False, "error": "source and destination are the same instance"}
        if not self.instance_cache.get(from_id, {}).get("connected"):
            return {"ok": False, "error": "source instance not connected"}
        if not self.instance_cache.get(to_id, {}).get("connected"):
            return {"ok": False, "error": "destination instance not connected"}
        dst_client = self.clients.get(to_id)
        src_client = self.clients.get(from_id)
        if src_client is None or dst_client is None:
            return {"ok": False, "error": "client not initialised"}

        src_torrents = {
            (t.get("hash") or "").lower(): t
            for t in self.instance_cache.get(from_id, {}).get("torrents", [])
        }
        moved, failed = [], []
        for h in hashes:
            h = h.lower()
            t = src_torrents.get(h)
            if t is None:
                failed.append({"hash": h, "error": "not found on the source instance"})
                continue
            name = t.get("name") or ""
            save_path = t.get("save_path") or ""
            category = t.get("category") or ""
            magnet = f"magnet:?xt=urn:btih:{h}&dn={quote(name)}"
            resp = await dst_client.add_url(magnet, save_path, category)
            if resp != "Ok.":
                failed.append({"hash": h, "error": f"destination add failed: {resp}"})
                continue
            await src_client.delete([h], delete_files=False)
            self.db.move_tracked(h, from_id, to_id)
            self.db.retarget_added(h, to_id)
            moved.append({"hash": h, "name": name})
            log.info("Moved %s -> instance %s (data kept at %s)", name, to_id, save_path or "?")
        return {"ok": True, "moved": moved, "failed": failed}

    # ---------------------------------------------------------------- views
    def status(self) -> dict:
        instances = []
        for inst in self.config.get("instances", []):
            iid = inst["id"]
            cache = self.instance_cache.get(iid, {})
            tor = cache.get("torrents", [])
            instances.append(
                {
                    "id": iid,
                    "name": inst.get("name", f"Instance {iid}"),
                    "url": inst.get("url", ""),
                    "connected": bool(cache.get("connected")),
                    "version": cache.get("version"),
                    "error": cache.get("error", ""),
                    "torrent_count": len(tor),
                    "categories": cache.get("categories", []),
                    "download_speed": sum(t.get("dlspeed", 0) or 0 for t in tor),
                    "upload_speed": sum(t.get("upspeed", 0) or 0 for t in tor),
                }
            )
        return {
            "instances": instances,
            "trackers": [
                {
                    "id": tr["id"],
                    "name": tr.get("name", f"Tracker {tr['id']}"),
                    "max_slots": tr.get("max_slots"),
                    "seed_hours": tr.get("seed_hours"),
                    "public": bool(tr.get("public")),
                }
                for tr in self.config.get("trackers", [])
            ],
            "settings": {
                "poll_interval_seconds": self.config.get("poll_interval_seconds", 30),
                "rss_scan_interval_minutes": self.config.get("rss_scan_interval_minutes", 15),
                "data_folder": self.config.get("data_folder", "/data/torrents"),
            },
        }

    def torrents(self, instance_id=None) -> list[dict]:
        feeds_by_id = {f["id"]: f for f in self.config.get("feeds", [])}
        trackers_by_id = {tr["id"]: tr for tr in self.config.get("trackers", [])}
        feed_by_hash = {}
        tracker_by_hash = {}
        for row in self.db.tracked_all():
            key = (row["hash"].lower(), row["instance_id"])
            if row["feed_id"] is not None:
                feed_by_hash[key] = row["feed_id"]
            if row["tracker_id"] is not None:
                tracker_by_hash[key] = row["tracker_id"]
        out = []
        for inst in self.config.get("instances", []):
            if instance_id is not None and inst["id"] != instance_id:
                continue
            cache = self.instance_cache.get(inst["id"], {})
            for t in cache.get("torrents", []):
                row = dict(t)
                row["instance_id"] = inst["id"]
                row["instance_name"] = inst.get("name", f"Instance {inst['id']}")
                row["instance_connected"] = bool(cache.get("connected"))
                key = ((t.get("hash") or "").lower(), inst["id"])
                fid = feed_by_hash.get(key)
                feed = feeds_by_id.get(fid) if fid else None
                tracker = trackers_by_id.get(feed.get("tracker_id")) if feed else None
                if tracker is None:
                    tid = tracker_by_hash.get(key)
                    tracker = trackers_by_id.get(tid) if tid else None
                row["tracker"] = (
                    tracker.get("name")
                    if tracker else (feed.get("name", "") if feed else "")
                )
                out.append(row)
        return out

    def rss_view(self) -> dict:
        feeds_out = []
        for feed in self.config.get("feeds", []):
            fid = feed["id"]
            items = self.db.items_for_feed(fid)
            instance = next(
                (i for i in self.config.get("instances", []) if i["id"] == feed["instance_id"]),
                None,
            )
            tracker = self._tracker_for_feed(feed)
            feeds_out.append(
                {
                    "id": fid,
                    "name": feed.get("name", ""),
                    "url": feed.get("url", ""),
                    "instance_id": feed["instance_id"],
                    "instance_name": instance.get("name") if instance else "?",
                    "savepath": feed.get("savepath", ""),
                    "category": feed.get("category", ""),
                    "tracker_id": feed.get("tracker_id"),
                    "tracker": (
                        {
                            "id": tracker["id"],
                            "name": tracker.get("name", ""),
                            "max_slots": tracker.get("max_slots"),
                            "seed_hours": tracker.get("seed_hours"),
                            "public": bool(tracker.get("public")),
                        }
                        if tracker else None
                    ),
                    "max_slots": tracker.get("max_slots") if tracker else None,
                    "seed_hours": tracker.get("seed_hours") if tracker else None,
                    "public": bool(tracker.get("public")) if tracker else False,
                    "enabled": bool(feed.get("enabled", True)),
                    "last_scan": feed.get("last_scan"),
                    "items": items,
                }
            )
        return {"feeds": feeds_out}
