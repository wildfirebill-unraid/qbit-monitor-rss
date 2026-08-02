"""RSS feed fetching and parsing (RSS 2.0 and Atom)."""

import logging
import re
import xml.etree.ElementTree as ET

import httpx

log = logging.getLogger("rss")

NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _local(tag: str) -> str:
    """Strip namespace prefix from an XML tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_text(node, *names, default=""):
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text.strip()
        # namespace-agnostic search
        for child in node:
            if _local(child.tag) == name and child.text:
                return child.text.strip()
    return default


def _find_enclosure(node):
    for child in node:
        if _local(child.tag) == "enclosure":
            url = child.get("url")
            if url:
                return url
    return None


def parse_feed(text: str) -> list[dict]:
    """Parse RSS/Atom XML into a list of items.

    Each item: {guid, title, link, pub_date, torrent_url}
    torrent_url prefers an <enclosure> url, then a .torrent-looking guid/link.
    """
    items = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        log.warning("RSS parse error: %s", exc)
        return items

    entries = []
    for child in root.iter():
        if _local(child.tag) == "item":
            entries.append(child)
    if not entries:
        for child in root.iter():
            if _local(child.tag) == "entry":
                entries.append(child)

    for node in entries:
        title = _find_text(node, "title")
        link = _find_text(node, "link")
        if not link:
            # Atom uses <link href="..."/> (attribute, no text)
            for child in node:
                if _local(child.tag) == "link":
                    href = child.get("href")
                    if href:
                        link = href
                        break
        pub_date = _find_text(node, "pubDate", "published", "updated", "dc:date")
        guid = _find_text(node, "guid")
        if not guid:
            # atom <id>
            for child in node:
                if _local(child.tag) == "id" and child.text:
                    guid = child.text.strip()
        if not guid:
            guid = link
        enclosure = _find_enclosure(node)
        # prefer a torrent download URL
        torrent_url = enclosure or link
        if not (title and guid):
            continue
        items.append(
            {
                "guid": guid,
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "torrent_url": torrent_url,
            }
        )
    return items


async def fetch_feed(url: str, timeout: float = 30.0) -> str | None:
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (qbit-monitor)"},
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text
    except httpx.HTTPError as exc:
        log.warning("Feed fetch failed %s: %s", url, exc)
        return None


async def download_torrent(url: str) -> bytes | None:
    """Download a .torrent file, returning raw bytes or None."""
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (qbit-monitor)"},
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except httpx.HTTPError as exc:
        log.warning("Torrent download failed %s: %s", url, exc)
        return None


def normalize_title(title: str) -> str:
    """Normalize a release title for loose duplicate comparison."""
    t = title.lower()
    # strip brackets and parenthesised qualifiers first, keep the rest
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()
