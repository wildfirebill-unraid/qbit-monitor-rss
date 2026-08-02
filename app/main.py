"""FastAPI application: serves the web GUI and the JSON API."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config as configmod
from .db import DB
from .manager import Manager
from .qbit import QBittorrent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

APP_NAME = "qbit-monitor-rss"
APP_VERSION = os.environ.get("APP_VERSION") or "dev"
APP_CREATOR = "wildfirebill"
APP_REPO_URL = "https://github.com/wildfirebill-unraid/qbit-monitor-rss"
APP_ISSUES_URL = APP_REPO_URL + "/issues"
APP_COPYRIGHT = "© 2026 wildfirebill"

cfg: dict = {}
db: DB | None = None
manager: Manager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global cfg, db, manager
    cfg = configmod.load_config()
    configmod.save_config(cfg)  # writes a default config on first run
    db = DB(os.path.join(os.environ.get("DATA_DIR", "./data"), "app.db"))
    db.connect()
    manager = Manager(cfg, db)
    await manager.start()
    log.info("qbit-monitor started (max slots=%s, seed hours=%s)", cfg.get("max_slots"), cfg.get("seed_hours"))
    yield
    if manager:
        await manager.stop()
    if db:
        db.close()


app = FastAPI(title="qbit-monitor", lifespan=lifespan)


def save_cfg():
    configmod.save_config(cfg)
    if manager:
        manager.reload()


# --------------------------------------------------------------------- static
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# -------------------------------------------------------------------- models
class InstanceIn(BaseModel):
    id: int | None = None
    name: str = ""
    url: str = ""
    username: str = ""
    password: str = ""


class FeedIn(BaseModel):
    id: int | None = None
    name: str = ""
    url: str = ""
    instance_id: int = 1
    savepath: str = ""
    category: str = ""
    max_slots: int | None = None
    enabled: bool = True


class SettingsIn(BaseModel):
    max_slots: int | None = Field(default=None, ge=50, le=200)
    seed_hours: float | None = Field(default=None, ge=1.0, le=24 * 30)
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    rss_scan_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    data_folder: str | None = None


# --------------------------------------------------------------------- state
@app.get("/api/state")
async def api_state():
    return manager.status()


@app.get("/api/torrents")
async def api_torrents(instance: str = "all"):
    if instance == "all":
        return {"torrents": manager.torrents()}
    try:
        iid = int(instance)
    except ValueError:
        raise HTTPException(400, "bad instance id")
    return {"torrents": manager.torrents(instance_id=iid)}


@app.get("/api/rss")
async def api_rss():
    return manager.rss_view()


@app.get("/api/about")
async def api_about():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "creator": APP_CREATOR,
        "repo_url": APP_REPO_URL,
        "issues_url": APP_ISSUES_URL,
        "copyright": APP_COPYRIGHT,
    }


# ----------------------------------------------------------------- settings
@app.post("/api/settings")
async def api_settings(body: SettingsIn):
    data = body.model_dump(exclude_none=True)
    if "max_slots" in data:
        cfg["max_slots"] = max(1, min(cfg.get("max_slots_limit", 200), data["max_slots"]))
    if "seed_hours" in data:
        cfg["seed_hours"] = data["seed_hours"]
    if "poll_interval_seconds" in data:
        cfg["poll_interval_seconds"] = data["poll_interval_seconds"]
    if "rss_scan_interval_minutes" in data:
        cfg["rss_scan_interval_minutes"] = data["rss_scan_interval_minutes"]
    if "data_folder" in data:
        cfg["data_folder"] = data["data_folder"].strip() or "/data/torrents"
    save_cfg()
    return {"ok": True, "settings": manager.status()["settings"]}


# -------------------------------------------------------------- instances
@app.post("/api/instances/test")
async def api_instance_test(body: InstanceIn):
    client = QBittorrent(
        {"url": body.url, "username": body.username, "password": body.password}
    )
    try:
        ver = await client.version()
        if ver:
            return {"ok": True, "version": ver}
        ok = await client.login()
        return {"ok": ok, "error": "connected but login failed" if not ok else None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        await client.close()


@app.post("/api/instances/save")
async def api_instance_save(body: InstanceIn):
    if not body.url:
        raise HTTPException(400, "url is required")
    if body.id is None:
        new_id = max([i["id"] for i in cfg["instances"]], default=0) + 1
        cfg["instances"].append(
            {
                "id": new_id,
                "name": body.name or f"Instance {new_id}",
                "url": body.url,
                "username": body.username,
                "password": body.password,
            }
        )
    else:
        inst = next((i for i in cfg["instances"] if i["id"] == body.id), None)
        if not inst:
            raise HTTPException(404, "instance not found")
        inst.update(
            {
                "name": body.name,
                "url": body.url,
                "username": body.username,
                "password": body.password,
            }
        )
    save_cfg()
    return {"ok": True, "instances": manager.status()["instances"]}


@app.post("/api/instances/{iid}/delete")
async def api_instance_delete(iid: int):
    cfg["instances"] = [i for i in cfg["instances"] if i["id"] != iid]
    save_cfg()
    return {"ok": True}


# -------------------------------------------------------------------- feeds
@app.post("/api/feeds/save")
async def api_feed_save(body: FeedIn):
    if not body.url:
        raise HTTPException(400, "url is required")
    if body.id is None:
        new_id = max([f["id"] for f in cfg["feeds"]], default=0) + 1
        cfg["feeds"].append(
            {
                "id": new_id,
                "name": body.name or f"Feed {new_id}",
                "url": body.url,
                "instance_id": body.instance_id,
                "savepath": body.savepath,
                "category": body.category,
                "max_slots": body.max_slots,
                "enabled": body.enabled,
            }
        )
    else:
        feed = next((f for f in cfg["feeds"] if f["id"] == body.id), None)
        if not feed:
            raise HTTPException(404, "feed not found")
        feed.update(
            {
                "name": body.name,
                "url": body.url,
                "instance_id": body.instance_id,
                "savepath": body.savepath,
                "category": body.category,
                "max_slots": body.max_slots,
                "enabled": body.enabled,
            }
        )
    save_cfg()
    return {"ok": True, "feeds": manager.rss_view()["feeds"]}


@app.post("/api/feeds/{fid}/delete")
async def api_feed_delete(fid: int):
    cfg["feeds"] = [f for f in cfg["feeds"] if f["id"] != fid]
    if db:
        db.delete_items_for_feed(fid)
    save_cfg()
    return {"ok": True}


@app.post("/api/feeds/{fid}/toggle")
async def api_feed_toggle(fid: int):
    feed = next((f for f in cfg["feeds"] if f["id"] == fid), None)
    if not feed:
        raise HTTPException(404, "feed not found")
    feed["enabled"] = not feed.get("enabled", True)
    save_cfg()
    return {"ok": True, "enabled": feed["enabled"]}


@app.post("/api/feeds/{fid}/scan")
async def api_feed_scan(fid: int):
    feed = next((f for f in cfg["feeds"] if f["id"] == fid), None)
    if not feed:
        raise HTTPException(404, "feed not found")
    result = await manager.scan_feed(feed)
    if result.get("ok"):
        await manager._process_queue()
    return {"ok": True, "result": result}


# ------------------------------------------------------------------- rss items
@app.post("/api/rss/{guid}/action")
async def api_rss_action(guid: str, action: str = "ignore"):
    if action == "ignore":
        db.update_item(guid, state="ignored")
    elif action == "retry":
        db.update_item(guid, state="pending", error=None)
        await manager._process_queue()
    elif action == "add-now":
        items = db.pending_items()
        item = next((i for i in items if i["guid"] == guid), None)
        if item is None:
            cur = next(
                (
                    i
                    for feed in manager.rss_view()["feeds"]
                    for i in feed["items"]
                    if i["guid"] == guid
                ),
                None,
            )
            if not cur:
                raise HTTPException(404, "item not found")
            db.update_item(guid, state="pending", error=None)
            item = next(
                (i for i in db.pending_items() if i["guid"] == guid), None
            )
        if item:
            await manager._try_add(item)
    else:
        raise HTTPException(400, "unknown action")
    return {"ok": True}
