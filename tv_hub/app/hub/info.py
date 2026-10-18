"""Filminformation (längd, genre, handling, skådespelare …).

Toucans filmlista saknar längd och genre. De hämtas film för film i
bakgrunden, långsamt (en film i taget med paus), så att källan inte belastas.
Filmer i synliga kategorier och nyast tillagda först. Detaljsidan hämtar
direkt om informationen saknas.
"""
import os
import re

from . import catalog, db, portals
from .catalog import minutes_from, _float
from .xtream import XtreamError

PAUSE_S = float(os.environ.get("INFO_PAUSE", "1.2"))         # mellan två hämtningar
START_DELAY_S = float(os.environ.get("INFO_START_DELAY", "60"))
RETRY_FAILED_DAYS = 7
_state = {"running": False}


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value or None


def _parse(info):
    duration = None
    secs = info.get("duration_secs")
    try:
        if secs not in (None, "") and int(secs) > 0:
            duration = round(int(secs) / 60)
    except (TypeError, ValueError):
        duration = None
    if duration is None:
        duration = minutes_from(info.get("duration") or info.get("episode_run_time"))
    release = info.get("releasedate") or info.get("release_date") or info.get("releaseDate") or None
    year = None
    if release:
        match = re.match(r"((?:19|20)\d{2})", str(release))
        year = int(match.group(1)) if match else None
    rating = _float(info.get("rating"))
    return {
        "duration_min": duration if duration and duration < 600 else None,
        "genre": info.get("genre") or None,
        "plot": info.get("plot") or info.get("description") or None,
        "actors": info.get("cast") or info.get("actors") or None,
        "director": info.get("director") or None,
        "release": release,
        "poster": info.get("movie_image") or info.get("cover_big") or None,
        "backdrop": _first(info.get("backdrop_path")),
        "trailer": info.get("youtube_trailer") or None,
        "country": info.get("country") or None,
        "year": year,
        "rating": rating if rating is not None and rating <= 10 else None,
    }


def _save(kind, item_id, state, fields=None):
    fields = fields or {}
    db.x(
        """INSERT OR REPLACE INTO item_info (type, item_id, state, fetched, duration_min, genre, plot, actors,
           director, release, poster, backdrop, trailer, country, year, rating)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (kind, str(item_id), state, db.iso(), fields.get("duration_min"), fields.get("genre"), fields.get("plot"),
         fields.get("actors"), fields.get("director"), fields.get("release"), fields.get("poster"),
         fields.get("backdrop"), fields.get("trailer"), fields.get("country"), fields.get("year"),
         fields.get("rating")),
    )


def fetch_vod(item_id, timeout=None):
    portal = portals.best_portal()
    if portal is None:
        return False
    try:
        info = portals.client(portal, timeout=timeout).vod_info(item_id, timeout=timeout)
    except XtreamError:
        _save("vod", item_id, -1)
        return False
    _save("vod", item_id, 1, _parse(info))
    return True


def _next_missing():
    return db.q1(
        """SELECT i.item_id FROM items i
           LEFT JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
           LEFT JOIN category_prefs p ON p.type = i.type AND p.category_id = i.category_id
           WHERE i.type = 'vod' AND (f.item_id IS NULL OR (f.state = -1 AND f.fetched < ?))
           ORDER BY COALESCE(p.hidden, 0), i.adult, i.added DESC
           LIMIT 1""",
        (db.ago(days=RETRY_FAILED_DAYS),),
    )


def fetch_loop(stop_event):
    stop_event.wait(START_DELAY_S)
    while not stop_event.is_set():
        if catalog.is_syncing() or portals.best_portal() is None:
            stop_event.wait(30)
            continue
        row = _next_missing()
        if row is None:
            _state["running"] = False
            stop_event.wait(600)
            continue
        _state["running"] = True
        try:
            fetch_vod(row["item_id"], timeout=20)
        except Exception as exc:  # loggas, loopen fortsätter
            print(f"[fel] filminfo {row['item_id']}: {exc}", flush=True)
            _save("vod", row["item_id"], -1)
        stop_event.wait(PAUSE_S)


def status():
    total = db.q1("SELECT COUNT(*) AS n FROM items WHERE type = 'vod'")["n"]
    done = db.q1(
        """SELECT COUNT(*) AS n FROM items i JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
           WHERE i.type = 'vod' AND f.state = 1"""
    )["n"]
    failed = db.q1(
        """SELECT COUNT(*) AS n FROM items i JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
           WHERE i.type = 'vod' AND f.state = -1"""
    )["n"]
    remaining = max(total - done - failed, 0)
    return {"total": total, "done": done, "failed": failed, "remaining": remaining,
            "hours_left": round(remaining * (PAUSE_S + 0.4) / 3600, 1), "running": _state["running"]}


def detail(kind, item_id):
    """Allt om en film eller serie. Hämtar filminfo direkt om den saknas."""
    item = catalog.get_item(kind, item_id)
    if item is None:
        return None
    row = db.q1("SELECT * FROM item_info WHERE type = ? AND item_id = ?", (kind, str(item_id)))
    if kind == "vod" and (row is None or row["state"] != 1):
        fetch_vod(item_id, timeout=12)
        row = db.q1("SELECT * FROM item_info WHERE type = ? AND item_id = ?", (kind, str(item_id)))
        item = catalog.get_item(kind, item_id)
    info = dict(row) if row else {}
    item.update({
        "plot": info.get("plot"), "actors": info.get("actors"), "director": info.get("director"),
        "release": info.get("release"), "poster": info.get("poster") or item.get("icon"),
        "backdrop": info.get("backdrop"), "trailer": info.get("trailer"), "country": info.get("country"),
    })
    item["favorite"] = (kind, str(item_id)) in catalog.favorite_keys()
    return item
