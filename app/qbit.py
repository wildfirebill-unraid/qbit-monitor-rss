"""Async client for the qBittorrent WebUI API."""

import logging

import httpx

log = logging.getLogger("qbit")


class QbitError(Exception):
    pass


class QBittorrent:
    def __init__(self, cfg: dict):
        self.url = cfg.get("url", "").rstrip("/")
        self.username = cfg.get("username", "")
        self.password = cfg.get("password", "")
        self._client = None
        self._authed = False

    async def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "qbit-monitor/1.0"},
            )
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._authed = False

    def close_safe(self):
        """Fire-and-forget close (safe to call from sync code)."""
        try:
            import asyncio

            if self._client is not None:
                loop = asyncio.get_event_loop()
                client = self._client
                self._client = None
                if loop.is_running():
                    loop.create_task(client.aclose())
                else:
                    loop.run_until_complete(client.aclose())
        except Exception:  # noqa: BLE001
            pass

    async def login(self) -> bool:
        if not self.url:
            return False
        client = await self._http()
        try:
            r = await client.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
            )
            if r.status_code == 200 and r.text.strip().lower() == "ok.":
                self._authed = True
                return True
            log.warning("qBittorrent login failed for %s: %s", self.url, r.text[:200])
        except httpx.HTTPError as exc:
            log.warning("qBittorrent unreachable %s: %s", self.url, exc)
        self._authed = False
        return False

    async def _ensure_auth(self):
        if self._authed:
            return True
        return await self.login()

    async def _request(self, method, path, **kwargs):
        client = await self._http()
        for attempt in range(2):
            r = await client.request(method, f"{self.url}{path}", **kwargs)
            if r.status_code in (403, 401) and attempt == 0:
                await self.login()
                continue
            return r
        return r

    async def version(self) -> str | None:
        try:
            r = await self._request("GET", "/api/v2/app/version")
            if r.status_code == 200:
                return r.text.strip()
        except httpx.HTTPError:
            return None
        return None

    async def torrents(self) -> list[dict]:
        """Return all torrents (raw dicts) or [] on failure."""
        if not await self._ensure_auth():
            return []
        try:
            r = await self._request("GET", "/api/v2/torrents/info")
            if r.status_code == 200:
                return r.json()
        except (httpx.HTTPError, ValueError):
            return []
        return []

    async def categories(self) -> list[str]:
        """Return the category names defined on this instance, or []."""
        if not await self._ensure_auth():
            return []
        try:
            r = await self._request("GET", "/api/v2/torrents/categories")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return sorted(data.keys())
        except (httpx.HTTPError, ValueError):
            return []
        return []

    async def torrents_by_hash(self, hashes: list[str]) -> list[dict]:
        """Fetch a subset of torrents by their info-hashes."""
        if not await self._ensure_auth():
            return []
        try:
            r = await self._request(
                "GET",
                "/api/v2/torrents/info",
                params={"hashes": "|".join(hashes)},
            )
            if r.status_code == 200:
                return r.json()
        except (httpx.HTTPError, ValueError):
            return []
        return []

    async def add_file(
        self,
        files: list[tuple[str, bytes]],
        save_path: str = "",
        category: str = "",
    ) -> str:
        """Upload one or more .torrent files. Returns a short status string."""
        if not await self._ensure_auth():
            return "auth failed"
        data = {}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        try:
            r = await self._request(
                "POST",
                "/api/v2/torrents/add",
                data=data,
                files=[
                    ("torrents", (name, payload, "application/x-bittorrent"))
                    for name, payload in files
                ],
            )
            return (r.text or "").strip()
        except httpx.HTTPError as exc:
            log.warning("add_file failed %s: %s", self.url, exc)
            return f"error: {exc}"

    async def add_url(self, url: str, save_path: str = "", category: str = "") -> str:
        if not await self._ensure_auth():
            return "auth failed"
        data = {"urls": url}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        try:
            r = await self._request("POST", "/api/v2/torrents/add", data=data)
            return (r.text or "").strip()
        except httpx.HTTPError as exc:
            log.warning("add_url failed %s: %s", self.url, exc)
            return f"error: {exc}"

    async def delete(self, hashes: list[str], delete_files: bool = False) -> str:
        """Remove torrents. delete_files=False keeps the data on disk."""
        if not hashes:
            return ""
        if not await self._ensure_auth():
            return "auth failed"
        try:
            r = await self._request(
                "DELETE",
                "/api/v2/torrents/delete",
                params={
                    "hashes": "|".join(hashes),
                    "deleteFiles": "true" if delete_files else "false",
                },
            )
            return (r.text or "").strip()
        except httpx.HTTPError as exc:
            log.warning("delete failed %s: %s", self.url, exc)
            return f"error: {exc}"
