"""Background manager: polls qBittorrent instances, scans RSS feeds, manages
the limited-slot auto-download queue and cross-instance de-duplication.
"""

import asyncio
import logging
import time

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

    def _update_tracked(self):
        """Release slots for torrents that have seeded long enough or vanished."""
        threshold = self.config.get("seed_hours", 72.5) * 3600.0
        now = time.time()
        for t in self.db.tracked_all():
            if t["slot_released"]:
                continue
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
    def _feed_slot_limit(self, feed: dict) -> int:
        """Per-feed slot cap; falls back to the global cap when unset."""
        val = feed.get("max_slots")
        if val:
            return int(val)
        return int(self.config.get("max_slots", 50))

    async def _process_queue(self):
        max_slots = int(self.config.get("max_slots", 50))
        enabled_ids = [
            f["id"] for f in self.config.get("feeds", []) if f.get("enabled", True)
        ]
        feeds_by_id = {f["id"]: f for f in self.config.get("feeds", [])}
        for _ in range(200):  # safety cap
            if self.db.slots_in_use() >= max_slots:
                return
            pending = self.db.pending_items(feed_ids=enabled_ids)
            if not pending:
                return
            item = next(
                (i for i in pending if self._feed_slot_limit(feeds_by_id.get(i["feed_id"], {}))
                 > self.db.slots_in_use_for_feed(i["feed_id"])),
                None,
            )
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
            torrent_bytes, filename, save_path, feed.get("category", "")
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
        self.db.track_torrent(info_hash, instance["id"], item["title"], feed["id"])
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
        used = self.db.slots_in_use()
        max_slots = self.config.get("max_slots", 50)
        pending = len(
            self.db.pending_items(
                feed_ids=[f["id"] for f in self.config.get("feeds", []) if f.get("enabled", True)]
            )
        )
        return {
            "instances": instances,
            "slots": {
                "max": max_slots,
                "used": used,
                "available": max(0, max_slots - used),
                "seed_hours": self.config.get("seed_hours", 72.5),
                "queue": pending,
            },
            "settings": {
                "max_slots": max_slots,
                "max_slots_limit": self.config.get("max_slots_limit", 200),
                "seed_hours": self.config.get("seed_hours", 72.5),
                "poll_interval_seconds": self.config.get("poll_interval_seconds", 30),
                "rss_scan_interval_minutes": self.config.get("rss_scan_interval_minutes", 15),
                "data_folder": self.config.get("data_folder", "/data/torrents"),
            },
        }

    def torrents(self, instance_id=None) -> list[dict]:
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
            feeds_out.append(
                {
                    "id": fid,
                    "name": feed.get("name", ""),
                    "url": feed.get("url", ""),
                    "instance_id": feed["instance_id"],
                    "instance_name": instance.get("name") if instance else "?",
                    "savepath": feed.get("savepath", ""),
                    "category": feed.get("category", ""),
                    "max_slots": feed.get("max_slots"),
                    "enabled": bool(feed.get("enabled", True)),
                    "last_scan": feed.get("last_scan"),
                    "items": items,
                }
            )
        return {"feeds": feeds_out}
