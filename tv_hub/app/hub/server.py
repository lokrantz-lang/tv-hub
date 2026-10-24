"""HTTP-servrar.

Admin (port 8080): bara via Home Assistant Ingress. Webbadmin + admin-API.
  Samma port tar också emot appens anrop till /device/... via Nabu Casa (se remote.py),
  med boxens nyckel i X-TVHub-Token.
Enheter (port 8099): TV-boxar och Home Assistant-sensorer, med nyckel.
"""
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from concurrent.futures import ThreadPoolExecutor

from . import (VERSION, ai, limits, catalog, control, db, devices, diagnostics, extras, info, myprograms, portals, profiles,
               sports,
               recordings, reminders, remote, taste, updates)
from .config import CFG
from .xtream import XtreamError

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MAX_BODY = 4 * 1024 * 1024  # import av IMDb/Letterboxd kan vara några hundra kB


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _int_arg(query, name, default, low=0, high=10**6):
    try:
        return max(low, min(int(query.get(name, [default])[0]), high))
    except (TypeError, ValueError):
        return default


# ---------- cache för serier och EPG ----------
_epg_pool = ThreadPoolExecutor(max_workers=8)
_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl, producer):
    now_t = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now_t - hit[0] < ttl:
            return hit[1]
    value = producer()
    with _cache_lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (now_t, value)
    return value


def series_detail(series_id):
    def produce():
        portal = portals.best_portal()
        if portal is None:
            raise HttpError(503, "Ingen fungerande portal.")
        try:
            raw = portals.client(portal).series_info(series_id)
        except XtreamError as exc:
            raise HttpError(502, f"Källan: {diagnostics.explain(exc.kind)}")
        info = raw.get("info") or {}
        episodes = raw.get("episodes") or {}
        if isinstance(episodes, list):  # vissa paneler skickar en lista av listor
            grouped = {}
            for season_list in episodes:
                for ep in season_list or []:
                    grouped.setdefault(str(ep.get("season", 1)), []).append(ep)
            episodes = grouped
        seasons = []
        for season_key in sorted(episodes, key=lambda k: int(k) if str(k).isdigit() else 0):
            eps = []
            for ep in episodes[season_key] or []:
                ep_info = ep.get("info") or {}
                eps.append({
                    "id": str(ep.get("id")),
                    "num": ep.get("episode_num"),
                    "title": ep.get("title") or f"Avsnitt {ep.get('episode_num')}",
                    "ext": ep.get("container_extension") or "mp4",
                    "duration": ep_info.get("duration") if isinstance(ep_info, dict) else None,
                    "plot": ep_info.get("plot") if isinstance(ep_info, dict) else None,
                    "image": ep_info.get("movie_image") if isinstance(ep_info, dict) else None,
                })
            seasons.append({"season": season_key, "episodes": eps})
        return {
            "id": str(series_id),
            "name": info.get("name"),
            "plot": info.get("plot"),
            "cover": info.get("cover"),
            "backdrop": (info.get("backdrop_path") or [None])[0] if isinstance(info.get("backdrop_path"), list)
            else info.get("backdrop_path"),
            "genre": info.get("genre"),
            "rating": info.get("rating"),
            "seasons": seasons,
        }
    return cached(("series", str(series_id)), 1800, produce)


def epg(stream_id):
    def produce():
        portal = portals.best_portal()
        if portal is None:
            return []
        try:
            return portals.client(portal).short_epg(stream_id)
        except XtreamError:
            return []
    return cached(("epg", str(stream_id)), 300, produce)


# ---------- gemensam hanterare ----------
class BaseHandler(BaseHTTPRequestHandler):
    server_version = "TVHub"
    sys_version = ""
    routes = []
    timeout = 60  # sekunder per läsning från klienten: en tyst eller långsam klient håller inte kvar en tråd

    def log_message(self, fmt, *args):
        pass

    def client_ip(self):
        return self.client_address[0]

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "Ogiltig längd")
        if length < 0:
            raise HttpError(400, "Ogiltig längd")
        if length > MAX_BODY:
            raise HttpError(413, "För stor förfrågan")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise HttpError(400, "Ogiltig JSON")
        if not isinstance(data, dict):
            raise HttpError(400, "Förväntade ett JSON-objekt")
        return data

    def dispatch(self, method):
        parsed = urlparse(self.path)
        self.query = parse_qs(parsed.query)
        try:
            if not self.allowed():
                raise HttpError(403, "Åtkomst nekad")
            if self.routes is ADMIN and method in ("POST", "PUT", "DELETE", "PATCH") \
                    and int(self.headers.get("Content-Length") or 0) > 0 \
                    and "json" not in (self.headers.get("Content-Type") or "").lower():
                # Webbadminen skickar alltid JSON. Det stoppar enkla formulär från andra sidor (CSRF).
                raise HttpError(415, "Förväntade JSON")
            for route_method, pattern, handler in self.routes:
                if route_method != method:
                    continue
                match = pattern.match(parsed.path)
                if match:
                    result = handler(self, *match.groups())
                    if result is not None:
                        status, payload = result
                        self.send_json(status, payload)
                    return
            raise HttpError(404, "Hittades inte")
        except HttpError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except (ValueError, LookupError) as exc:
            self.send_json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # oväntat fel: logga, svara 500
            print(f"[fel] {method} {parsed.path}: {exc!r}", flush=True)
            try:
                self.send_json(500, {"error": "Internt fel"})
            except OSError:
                pass

    def allowed(self):
        return True

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def route(table, method, path):
    def decorator(fn):
        table.append((method, re.compile("^" + path + "$"), fn))
        return fn
    return decorator


# ====================================================================
# Admin
# ====================================================================
ADMIN = []


class AdminHandler(BaseHandler):
    routes = ADMIN

    def allowed(self):
        allowed = [ip.strip() for ip in CFG["admin_allowed_ips"].split(",") if ip.strip()]
        return "*" in allowed or self.client_ip() in allowed


@route(ADMIN, "GET", r"/(?:index\.html)?")
def admin_index(h):
    return serve_static(h, "index.html")


@route(ADMIN, "GET", r"/static/([\w.\-]+)")
def admin_static(h, name):
    return serve_static(h, name)


def serve_static(h, name):
    path = os.path.join(STATIC_DIR, name)
    if not os.path.isfile(path):
        raise HttpError(404, "Saknas")
    with open(path, "rb") as fh:
        body = fh.read()
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-cache")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    h.wfile.write(body)


@route(ADMIN, "GET", r"/api/overview")
def admin_overview(h):
    items, best = portals.summaries()
    return 200, {
        "version": VERSION,
        "diagnosis": diagnostics.overview(),
        "portals": items,
        "active_portal_id": best,
        "devices": devices.list_devices(),
        "sessions": devices.active_sessions(),
        "catalog": catalog.counts(),
        "sync": catalog.sync_status(),
        "last_sync": (catalog.sync_runs(1) or [None])[0],
        "device_port": CFG["device_port"],
        "info": info.status(),
        "app": updates.status(),
        "groups": [{"id": g["id"], "name": g["name"]} for g in catalog.groups()],
    }


# ----- portaler -----
@route(ADMIN, "GET", r"/api/portals")
def admin_portals(h):
    items, _ = portals.summaries()
    return 200, items


@route(ADMIN, "POST", r"/api/portals")
def admin_portal_create(h):
    data, error = portals.validate(h.read_json())
    if error:
        raise HttpError(400, error)
    try:
        pid = db.x(
            """INSERT INTO portals (name, url, username, password, priority, enabled, created)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (data["name"], data["url"], data["username"], data["password"],
             data.get("priority", 100), data.get("enabled", 1), db.iso()),
        ).lastrowid
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HttpError(409, "Det finns redan en portal med det namnet.")
        raise
    portals.run_check_now(pid)
    return 201, portals.summary(portals.get(pid))


@route(ADMIN, "PUT", r"/api/portals/(\d+)")
def admin_portal_update(h, pid):
    pid = int(pid)
    if not portals.get(pid):
        raise HttpError(404, "Portalen finns inte")
    data, error = portals.validate(h.read_json(), partial=True)
    if error:
        raise HttpError(400, error)
    if data:
        sets = ", ".join(f"{k} = ?" for k in data)
        try:
            db.x(f"UPDATE portals SET {sets} WHERE id = ?", (*data.values(), pid))
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HttpError(409, "Det finns redan en portal med det namnet.")
            raise
    return 200, portals.summary(portals.get(pid))


@route(ADMIN, "DELETE", r"/api/portals/(\d+)")
def admin_portal_delete(h, pid):
    with db.tx() as conn:
        conn.execute("DELETE FROM checks WHERE portal_id = ?", (int(pid),))
        cur = conn.execute("DELETE FROM portals WHERE id = ?", (int(pid),))
    if not cur.rowcount:
        raise HttpError(404, "Portalen finns inte")
    return 200, {"deleted": int(pid)}


@route(ADMIN, "POST", r"/api/portals/(\d+)/check")
def admin_portal_check(h, pid):
    result = portals.run_check_now(int(pid))
    if result is None:
        raise HttpError(404, "Portalen finns inte")
    result["error_text"] = diagnostics.explain(result["error_kind"]) if result["error_kind"] else None
    return 200, {"check": result, "portal": portals.summary(portals.get(int(pid)))}


@route(ADMIN, "GET", r"/api/portals/(\d+)/history")
def admin_portal_history(h, pid):
    return 200, diagnostics.portal_history(int(pid), _int_arg(h.query, "hours", 24, 1, 720))


# ----- katalog -----
@route(ADMIN, "POST", r"/api/catalog/sync")
def admin_sync(h):
    started = catalog.start_sync("manual")
    return 202, {"started": started, "message": None if started else "En synk pågår redan."}


@route(ADMIN, "GET", r"/api/catalog/syncs")
def admin_syncs(h):
    return 200, {"status": catalog.sync_status(), "runs": catalog.sync_runs(20)}


@route(ADMIN, "GET", r"/api/library/categories")
def admin_categories(h):
    kind = h.query.get("type", ["live"])[0]
    if kind not in catalog.TYPES:
        raise HttpError(400, "Okänd typ")
    if "adult" in h.query:
        # Biblioteket: bara kategorier med innehåll i valt läge (vanligt, 18+ eller allt).
        return 200, catalog.categories(kind, _admin_scope(h))
    return 200, catalog.categories(kind)


@route(ADMIN, "GET", r"/api/library/items")
def admin_items(h):
    kind = h.query.get("type", ["live"])[0]
    cid = h.query.get("category_id", [None])[0]
    if kind not in catalog.TYPES or cid is None:
        raise HttpError(400, "type och category_id krävs")
    return 200, catalog.items_in_category(kind, cid, _int_arg(h.query, "offset", 0), _int_arg(h.query, "limit", 200, 1, 1000))


@route(ADMIN, "GET", r"/api/library/search")
def admin_search(h):
    q = h.query.get("q", [""])[0]
    kind = h.query.get("type", [None])[0]
    return 200, catalog.search(q, kind, _int_arg(h.query, "limit", 100, 1, 500), _int_arg(h.query, "offset", 0))


def _admin_scope(h):
    """Innehåll i webbadminen: vanligt (standard), bara vuxen eller allt."""
    mode = h.query.get("adult", ["0"])[0]
    return catalog.Scope(adult=None if mode == "all" else (1 if mode == "1" else 0))


@route(ADMIN, "GET", r"/api/library/browse")
def admin_browse(h):
    kind = h.query.get("type", ["vod"])[0]
    if kind not in catalog.TYPES:
        raise HttpError(400, "Okänd typ")
    params = {k: h.query[k][0] for k in BROWSE_KEYS if k in h.query and h.query[k][0] != ""}
    items, total = catalog.browse(kind, _admin_scope(h), h.query.get("category_id", [None])[0] or None, params,
                                  _int_arg(h.query, "offset", 0), _int_arg(h.query, "limit", 120, 1, 500))
    if kind == "vod":
        for item in items:
            item["watched"] = extras.is_watched(1 if item.get("adult") else 0, kind, item["id"])
    return 200, {"items": items, "total": total}


@route(ADMIN, "GET", r"/api/library/genres")
def admin_genres(h):
    kind = h.query.get("type", ["vod"])[0]
    if kind not in catalog.TYPES:
        raise HttpError(400, "Okänd typ")
    return 200, catalog.genres(kind, _admin_scope(h))


@route(ADMIN, "GET", r"/api/library/vod/([\w\-]+)")
def admin_vod(h, vod_id):
    item = info.detail("vod", vod_id)
    if item is None:
        raise HttpError(404, "Finns inte.")
    return 200, item


IMG_DIR = None
IMG_MAX_BYTES = 3 * 1024 * 1024
IMG_MAX_FILES = 4000
IMG_MAX_TOTAL = 256 * 1024 * 1024   # cachens storlek i byte
IMG_MIN_FREE = 500 * 1024 * 1024    # ingen cache om disken har mindre än så här ledigt


def _portal_hosts():
    out = []
    try:
        for row in db.q("SELECT url FROM portals"):
            parts = urlparse(row["url"] if "://" in row["url"] else "http://" + row["url"])
            if parts.hostname:
                port = parts.port or (443 if parts.scheme == "https" else 80)
                out.append({"host": f"{parts.hostname.lower()}:{port}"})
    except Exception:  # noqa: BLE001
        pass
    return out


def _img_cache_store(path, body):
    """Sparar en bild i cachen om det finns plats (antal filer, byte och ledigt diskutrymme)."""
    import shutil
    try:
        if shutil.disk_usage(IMG_DIR).free < IMG_MIN_FREE:
            return
        with open(path, "wb") as fh:
            fh.write(body)
        entries = [(e.path, e.stat()) for e in os.scandir(IMG_DIR) if e.is_file()]
        total = sum(st.st_size for _, st in entries)
        if len(entries) > IMG_MAX_FILES or total > IMG_MAX_TOTAL:
            entries.sort(key=lambda item: item[1].st_mtime)
            for old, st in entries:
                if len(entries) <= IMG_MAX_FILES - 200 and total <= IMG_MAX_TOTAL * 0.8:
                    break
                os.remove(old)
                total -= st.st_size
                entries = [e for e in entries if e[0] != old]
    except OSError:
        pass


@route(ADMIN, "GET", r"/api/img")
def admin_img(h):
    """Hämtar affischer via servern. Källans bilder ligger ofta på http, vilket webbläsaren
    blockerar när webbadminen öppnas via https (Nabu Casa). Bilderna sparas i en cache."""
    import hashlib
    import urllib.request
    global IMG_DIR
    url = h.query.get("u", [""])[0]
    if not re.match(r"^https?://", url) or len(url) > 1000:
        raise HttpError(400, "Ogiltig bildadress")
    if IMG_DIR is None:
        IMG_DIR = os.path.join(CFG["data_dir"], "imgcache")
        os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest())
    body, ctype = None, "image/jpeg"
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            body = fh.read()
        ctype = "image/png" if body[:4] == b"\x89PNG" else ("image/webp" if body[8:12] == b"WEBP" else "image/jpeg")
    else:
        try:
            # Bara publika adresser: hemnätet, loopback och Home Assistants interna tjänster spärras,
            # också via omdirigeringar. Källans egen värd är undantagen.
            own_hosts = {p_["host"] for p_ in _portal_hosts()}
            res = limits.open_public(url, {"User-Agent": CFG["user_agent"]}, 8, allow_hosts=own_hosts)
            with res:
                ctype = (res.headers.get("Content-Type") or "").split(";")[0].strip()
                if not ctype.startswith("image/") or ctype == "image/svg+xml":
                    raise HttpError(415, "Inte en bild")
                body = res.read(IMG_MAX_BYTES + 1)
        except HttpError:
            raise
        except limits.BlockedAddress:
            raise HttpError(400, "Adressen är inte tillåten")
        except Exception:
            raise HttpError(502, "Bilden kunde inte hämtas")
        if len(body) > IMG_MAX_BYTES:
            raise HttpError(413, "För stor bild")
        _img_cache_store(path, body)
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "private, max-age=604800")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    h.wfile.write(body)
    return None


@route(ADMIN, "GET", r"/api/library/series/([\w\-]+)")
def admin_series(h, sid):
    return 200, series_detail(sid)


# ----- favoriter -----
@route(ADMIN, "GET", r"/api/favorites")
def admin_favorites(h):
    return 200, catalog.favorites()


@route(ADMIN, "POST", r"/api/favorites")
def admin_favorite_set(h):
    data = h.read_json()
    catalog.set_favorite(data.get("type"), data.get("id"), bool(data.get("on", True)))
    return 200, {"ok": True}


# ----- grupper -----
@route(ADMIN, "GET", r"/api/groups")
def admin_groups(h):
    return 200, catalog.groups(with_items=True)


@route(ADMIN, "POST", r"/api/groups")
def admin_group_create(h):
    data = h.read_json()
    try:
        gid = catalog.create_group(data.get("name"), data.get("sort", 100))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HttpError(409, "Det finns redan en grupp med det namnet.")
        raise
    return 201, {"id": gid}


@route(ADMIN, "PUT", r"/api/groups/(\d+)")
def admin_group_update(h, gid):
    data = h.read_json()
    try:
        catalog.update_group(int(gid), data.get("name"), data.get("sort"))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HttpError(409, "Det finns redan en grupp med det namnet.")
        raise
    if "order" in data and isinstance(data["order"], list):
        catalog.reorder_group(int(gid), data["order"])
    return 200, {"ok": True}


@route(ADMIN, "DELETE", r"/api/groups/(\d+)")
def admin_group_delete(h, gid):
    if not catalog.delete_group(int(gid)):
        raise HttpError(404, "Gruppen finns inte")
    return 200, {"ok": True}


@route(ADMIN, "POST", r"/api/groups/(\d+)/items")
def admin_group_add(h, gid):
    data = h.read_json()
    catalog.add_to_group(int(gid), data.get("type"), data.get("id"))
    return 200, {"ok": True}


@route(ADMIN, "DELETE", r"/api/groups/(\d+)/items/(live|vod|series)/([\w\-]+)")
def admin_group_remove(h, gid, kind, item_id):
    catalog.remove_from_group(int(gid), kind, item_id)
    return 200, {"ok": True}


# ----- enheter -----
@route(ADMIN, "GET", r"/api/devices")
def admin_devices(h):
    return 200, devices.list_devices()


@route(ADMIN, "POST", r"/api/devices/pair-code")
def admin_pair_code(h):
    return 200, devices.new_pair_code()


@route(ADMIN, "POST", r"/api/devices/token")
def admin_device_token(h):
    data = h.read_json()
    return 200, devices.create_token_device(str(data.get("name") or "Home Assistant"))


@route(ADMIN, "PUT", r"/api/devices/(\d+)")
def admin_device_update(h, did):
    devices.update_device(int(did), h.read_json())
    return 200, {"ok": True}


@route(ADMIN, "DELETE", r"/api/devices/(\d+)")
def admin_device_delete(h, did):
    if not devices.remove(int(did)):
        raise HttpError(404, "Enheten finns inte")
    return 200, {"ok": True}


# ----- kategorier (dölj och sortera för boxarna) -----
@route(ADMIN, "GET", r"/api/categories")
def admin_category_prefs(h):
    kind = h.query.get("type", ["live"])[0]
    if kind not in catalog.TYPES:
        raise HttpError(400, "Okänd typ")
    return 200, catalog.categories(kind)


@route(ADMIN, "PUT", r"/api/categories/start")
def admin_category_start(h):
    data = h.read_json()
    return 200, catalog.set_start_category(data.get("type"), data.get("category_id"))


@route(ADMIN, "POST", r"/api/categories/prefs")
def admin_category_prefs_set(h):
    data = h.read_json()
    changes = data.get("changes")
    if not isinstance(changes, list) or len(changes) > 5000:
        raise HttpError(400, "changes måste vara en lista")
    catalog.set_category_prefs(data.get("type"), changes)
    return 200, {"ok": True}


# ----- sparade filter -----
@route(ADMIN, "GET", r"/api/filters")
def admin_filters(h):
    return 200, extras.saved_filters()


@route(ADMIN, "DELETE", r"/api/filters/(\d+)")
def admin_filter_delete(h, fid):
    if not extras.delete_filter(int(fid)):
        raise HttpError(404, "Finns inte")
    return 200, {"ok": True}


# ----- bevakningar -----
@route(ADMIN, "GET", r"/api/watches")
def admin_watches(h):
    return 200, extras.watches()


@route(ADMIN, "POST", r"/api/watches")
def admin_watch_create(h):
    data = h.read_json()
    return 201, extras.create_watch(data.get("query"), data.get("type"))


@route(ADMIN, "DELETE", r"/api/watches/(\d+)")
def admin_watch_delete(h, wid):
    if not extras.delete_watch(int(wid)):
        raise HttpError(404, "Finns inte")
    return 200, {"ok": True}


@route(ADMIN, "POST", r"/api/watches/check")
def admin_watch_check(h):
    hits = extras.check_watches()
    return 200, {"new": [item["name"] for _, item in hits]}


# ----- inspelningar -----
@route(ADMIN, "GET", r"/api/recordings")
def admin_recordings(h):
    return 200, recordings.all_recordings()


@route(ADMIN, "POST", r"/api/recordings")
def admin_recording_create(h):
    data = h.read_json()
    try:
        device_id = int(data.get("device_id"))
    except (TypeError, ValueError):
        raise HttpError(400, "Välj en box.")
    dev = db.q1("SELECT * FROM devices WHERE id = ?", (device_id,))
    if not dev or dev["model"] == "integration":
        raise HttpError(400, "Boxen finns inte.")
    kind = data.get("kind") or "live"
    if kind in ("vod", "series"):
        ref = catalog.get_item(kind, str(data.get("id") or ""))
        if ref is None:
            raise HttpError(404, "Finns inte.")
        scope = devices.scope_for(dev, bool(ref.get("adult")))
        if bool(scope.adult) != bool(ref.get("adult")):
            raise HttpError(400, "Boxens profil tillåter inte det här.")
        if not catalog.in_scope(kind, ref["id"], scope):
            raise HttpError(400, "Boxens profil tillåter inte det här.")
        if kind == "series":
            return 201, recordings.create_series(dev, scope, ref["id"], series_detail(ref["id"]), data.get("season"))
        rec, created = recordings.create_media(dev, scope, "vod", ref["id"])
        return (201 if created else 200), rec
    channel = catalog.get_item("live", str(data.get("stream_id") or ""))
    if channel is None:
        raise HttpError(404, "Kanalen finns inte.")
    scope = devices.scope_for(dev, bool(channel.get("adult")))
    if bool(scope.adult) != bool(channel.get("adult")):
        raise HttpError(400, "Boxens profil tillåter inte den här kanalen.")
    rec, created = recordings.create(dev, scope, channel["id"], data.get("title"),
                                     data.get("start"), data.get("end"))
    return (201 if created else 200), rec


@route(ADMIN, "POST", r"/api/recordings/(\d+)/stop")
def admin_recording_stop(h, rid):
    recordings.stop(int(rid))
    return 200, {"ok": True}


@route(ADMIN, "DELETE", r"/api/recordings/(\d+)")
def admin_recording_delete(h, rid):
    return 200, {"result": recordings.remove(int(rid))}


@route(ADMIN, "GET", r"/api/guide/([\w\-]+)")
def admin_guide(h, stream_id):
    if catalog.get_item("live", stream_id) is None:
        raise HttpError(404, "Kanalen finns inte.")
    return 200, guide_for(stream_id)


# ----- diagnostik -----
# ----- telefonen utanför hemmet (Nabu Casa) -----
@route(ADMIN, "GET", r"/api/remote")
def admin_remote(h):
    return 200, remote.settings()


@route(ADMIN, "PUT", r"/api/remote")
def admin_remote_save(h):
    return 200, remote.save_settings(h.read_json())


@route(ADMIN, "POST", r"/api/remote/test")
def admin_remote_test(h):
    return 200, remote.test_path()


@route(ADMIN, "GET", r"/api/diagnostics")
def admin_diagnostics(h):
    return 200, {
        "diagnosis": diagnostics.overview(),
        "events": diagnostics.recent_events(_int_arg(h.query, "limit", 100, 1, 500)),
        "net": db.rows(db.q("SELECT ts, ok, ms FROM net_checks ORDER BY id DESC LIMIT 60")),
    }


@route(ADMIN, "POST", r"/api/diagnostics/capacity-test")
def admin_capacity(h):
    data = h.read_json()
    return 200, diagnostics.capacity_test(data.get("type", "vod"), data.get("id"), data.get("seconds", 8))


# ====================================================================
# Enheter (TV-boxar)
# ====================================================================
DEVICE = []


class DeviceHandler(BaseHandler):
    routes = DEVICE
    via_ingress = False

    def auth_header(self):
        return self.headers.get("Authorization")

    def device_ip(self):
        return self.client_ip()

    def device(self):
        cached = getattr(self, "_device_row", None)
        if cached is not None:
            return cached
        dev = devices.authenticate(self.auth_header(), self.device_ip())
        if dev is None:
            raise HttpError(401, "Boxen är inte parad. Para om den i appen.")
        if self.via_ingress:
            remote.mark_remote(dev, self.headers, self.device_ip())
        self._device_row = dev
        return dev

    def profile(self):
        """Vem som tittar (X-TVHub-Profile; äldre appar skickar X-TVHub-Viewer). Annars boxens standardprofil."""
        cached = getattr(self, "_profile", None)
        if cached is not None:
            return cached
        value = (self.headers.get("X-TVHub-Profile") or self.headers.get("X-TVHub-Viewer") or "").strip()
        self._profile = profiles.resolve(value, self.device())
        return self._profile

    def wants_adult(self):
        """Läget skickas av appen i varje anrop. Allt annat än "adult" betyder vanligt läge."""
        return (self.headers.get("X-TVHub-Mode") or "").strip().lower() == "adult"

    def wants_history(self):
        """Vuxenläget: "show" visar favoriter och historik, annars döljs de. Sparas gör de alltid."""
        return (self.headers.get("X-TVHub-History") or "").strip().lower() == "show"

    def context(self):
        dev = self.device()
        prof = self.profile()
        # Vuxenläget: bara profiler som får använda det, och med PIN-koden om boxen kräver den.
        adult = self.wants_adult() and prof.get("allow_adult") and \
            devices.adult_unlocked(dev, (self.headers.get("X-TVHub-Unlock") or "").strip())
        return dev, devices.scope_for(dev, adult, self.wants_history(), prof)

    def viewer_id(self):
        """Filmkväll: profilen som tittar (None för gäst, som inte sparar något)."""
        try:
            prof = self.profile()
        except HttpError:
            return taste.resolve_viewer((self.headers.get("X-TVHub-Viewer") or "").strip())
        return None if prof.get("guest") else (prof.get("id") or None)


@route(DEVICE, "GET", r"/device/ping")
def dev_ping(h):
    return 200, {"app": "tvhub", "version": VERSION}


@route(DEVICE, "POST", r"/device/pair")
def dev_pair(h):
    data = h.read_json()
    try:
        result = devices.pair(data.get("code"), data.get("name"), data.get("model"),
                              data.get("app_version"), h.client_ip())
    except devices.TooManyAttempts as exc:
        raise HttpError(429, str(exc))
    if result is None:
        time.sleep(1)  # bromsar gissning av koder
        raise HttpError(403, "Fel eller utgången kod. Skapa en ny kod i webbadminen.")
    return 200, result


@route(DEVICE, "GET", r"/device/home")
def dev_home(h):
    dev, scope = h.context()
    if h.query.get("app_version"):
        db.x("UPDATE devices SET app_version = ? WHERE id = ?", (h.query["app_version"][0][:20], dev["id"]))
    group_only = scope.group_id is not None
    return 200, {
        "device": {"id": dev["id"], "name": dev["name"]},
        "mode": "adult" if scope.adult else "normal",
        "history": not scope.private,
        "profile": {"allow_adult": bool(dev["allow_adult"]), "only_group_id": dev["only_group_id"]},
        "continue": [] if scope.private else extras.continue_watching(scope),
        "recent_live": [] if scope.private else extras.recent_channels(dev["id"], scope),
        "favorites": [] if scope.private else catalog.favorites(scope) + extras.episode_favorites(scope),
        "groups": catalog.groups(with_items=True, scope=scope),
        "saved_filters": [] if group_only else extras.saved_filters(scope.adult),
        "watch_hits": [] if group_only or scope.adult else extras.recent_hits(scope),
        "categories": {kind: catalog.categories(kind, scope) for kind in catalog.TYPES},
        "start_categories": {kind: catalog.start_category(kind) for kind in catalog.TYPES if catalog.start_category(kind)},
        "recent_vod": [] if group_only else catalog.recent("vod", 40, scope),
        # Rader för den nya hemskärmen
        "recent_series": [] if group_only else catalog.recent("series", 30, scope),
        "top_vod": [] if group_only else catalog.browse(
            "vod", scope, None, {"sort": "rating", "min_rating": "7.5", "min_year": str(time.gmtime().tm_year - 6)}, 0, 30)[0],
        "counts": catalog.counts(scope),
        "recordings": len(recordings.for_device(dev["id"], adult=scope.adult or 0)),
        "adult_pin": devices.adult_pin_set() and bool(dev["pin_required"]),
        # Profiler: vem som tittar, och vad boxen går tillbaka till efter en stund utan användning.
        "active_profile": h.profile(),
        "profiles": profiles.all_profiles(),
        "box": {"default_profile_id": dev["default_profile_id"] or profiles.owner_id(), "revert_hours": dev["revert_hours"],
                "adult_idle_minutes": dev["adult_idle_minutes"]},
    }


BROWSE_KEYS = ("sort", "min_rating", "min_year", "max_year", "min_duration", "max_duration", "genre", "q", "new_only")


@route(DEVICE, "GET", r"/device/browse")
def dev_browse(h):
    """Bläddra med sortering och filter. type krävs, category_id valfritt."""
    dev, scope = h.context()
    kind = h.query.get("type", ["vod"])[0]
    if kind not in catalog.TYPES:
        raise HttpError(400, "Okänd typ")
    params = {k: h.query[k][0] for k in BROWSE_KEYS if k in h.query}
    items, total = catalog.browse(kind, scope, h.query.get("category_id", [None])[0], params,
                                  _int_arg(h.query, "offset", 0), _int_arg(h.query, "limit", 300, 1, 1000))
    if kind != "live":
        for item in items:
            item["watched"] = extras.is_watched(scope.adult or 0, kind, item["id"], scope.pid) \
                if kind == "vod" and not scope.private else False
    return 200, {"items": items, "total": total}


@route(DEVICE, "GET", r"/device/items")
def dev_items(h):
    dev, scope = h.context()
    kind = h.query.get("type", ["live"])[0]
    cid = h.query.get("category_id", [None])[0]
    if kind not in catalog.TYPES or cid is None:
        raise HttpError(400, "type och category_id krävs")
    return 200, catalog.items_in_category(kind, cid, _int_arg(h.query, "offset", 0),
                                          _int_arg(h.query, "limit", 500, 1, 5000), scope)


@route(DEVICE, "GET", r"/device/genres")
def dev_genres(h):
    dev, scope = h.context()
    kind = h.query.get("type", ["vod"])[0]
    if kind not in catalog.TYPES:
        raise HttpError(400, "Okänd typ")
    return 200, catalog.genres(kind, scope)


@route(DEVICE, "GET", r"/device/search")
def dev_search(h):
    dev, scope = h.context()
    return 200, catalog.search(h.query.get("q", [""])[0], h.query.get("type", [None])[0],
                               _int_arg(h.query, "limit", 80, 1, 300), scope=scope)


@route(DEVICE, "GET", r"/device/vod/([\w\-]+)")
def dev_vod(h, vod_id):
    dev, scope = h.context()
    if not catalog.in_scope("vod", vod_id, scope):
        raise HttpError(404, "Finns inte.")
    item = info.detail("vod", vod_id)
    if item is None:
        raise HttpError(404, "Finns inte.")
    adult = scope.adult or 0
    item["resume"] = None if scope.private else extras.resume_position(adult, "vod", vod_id, scope.pid)
    item["watched"] = False if scope.private else extras.is_watched(adult, "vod", vod_id, scope.pid)
    item["favorite"] = False if scope.private else ("vod", str(vod_id)) in catalog.favorite_keys(scope.pid)
    if not scope.adult:
        item.update(taste.detail_extras("vod", vod_id, h.viewer_id()))
    return 200, item


@route(DEVICE, "GET", r"/device/series/([\w\-]+)")
def dev_series(h, sid):
    dev, scope = h.context()
    if catalog.get_item("series", sid) is not None and not catalog.in_scope("series", sid, scope):
        raise HttpError(404, "Finns inte.")
    detail = dict(series_detail(sid))
    meta = info.detail("series", sid) or {}
    for key in ("plot", "genre", "actors", "director", "release", "year", "rating_num", "duration_min", "favorite"):
        if meta.get(key) and not detail.get(key):
            detail[key] = meta[key]
    detail["cover"] = detail.get("cover") or meta.get("poster") or meta.get("icon")
    progress, watched = ({}, []) if scope.private else extras.series_progress(scope.adult or 0, sid, scope.pid)
    detail["progress"] = progress
    detail["watched"] = watched
    detail["favorite_episodes"] = [] if scope.private else extras.episode_favorite_ids(scope.adult, sid, scope.pid)
    detail["favorite"] = False if scope.private else ("series", str(sid)) in catalog.favorite_keys(scope.pid)
    if not scope.adult:
        detail.update(taste.detail_extras("series", sid, h.viewer_id()))
    return 200, detail


def _live_visible(scope, stream_id):
    """Kanaler som inte hör till boxens läge och profil visas inte (inte heller deras programguide)."""
    return catalog.in_scope("live", stream_id, scope) or \
        (catalog.get_item("live", stream_id) is None and scope.group_id is None and not scope.adult)


@route(DEVICE, "GET", r"/device/epg/([\w\-]+)")
def dev_epg(h, stream_id):
    dev, scope = h.context()
    if not _live_visible(scope, stream_id):
        return 200, []
    return 200, epg(stream_id)


@route(DEVICE, "GET", r"/device/epg-now")
def dev_epg_now(h):
    """Nu och nästa för upp till 40 kanaler i ett anrop (för kanallistan)."""
    dev, scope = h.context()
    ids = [i for i in (h.query.get("ids", [""])[0]).split(",") if re.match(r"^[\w\-]{1,40}$", i)][:40]
    ids = [i for i in ids if _live_visible(scope, i)]
    now_s = time.time()
    futures = {i: _epg_pool.submit(epg, i) for i in ids}
    out = {}
    for stream_id, future in futures.items():
        try:
            listings = future.result(timeout=15)
        except Exception:
            listings = []
        upcoming = [p for p in listings if p.get("end") and p["end"] > now_s]
        out[stream_id] = {"now": upcoming[0] if upcoming else None,
                          "next": upcoming[1] if len(upcoming) > 1 else None}
    return 200, out


def guide_for(stream_id):
    """Programguide för en kanal: bakåt så långt catch-up räcker, och ett och ett halvt dygn framåt."""
    channel = catalog.get_item("live", stream_id) or {}
    archive_days = min(int(channel.get("archive_days") or 0), 14)
    now_s = time.time()
    since = now_s - max(archive_days, 0.25) * 86400
    until = now_s + 36 * 3600

    def produce():
        portal = portals.best_portal()
        if portal is None:
            return []
        try:
            return portals.client(portal).full_epg(stream_id)
        except XtreamError:
            return []

    programs = []
    for p in cached(("guide", str(stream_id)), int(os.environ.get("GUIDE_CACHE_S", "600")), produce):
        if p["end"] < since or p["start"] > until:
            continue
        item = dict(p)
        item["now"] = p["start"] <= now_s < p["end"]
        item["playable"] = bool(archive_days and p["end"] <= now_s and (p["archive"] or archive_days))
        item["recordable"] = p["end"] > now_s or item["playable"]
        programs.append(item)
    return {"channel": channel.get("name"), "archive_days": archive_days, "programs": programs}


@route(DEVICE, "GET", r"/device/guide/([\w\-]+)")
def dev_guide(h, stream_id):
    dev, scope = h.context()
    if not catalog.in_scope("live", stream_id, scope):
        raise HttpError(404, "Finns inte.")
    return 200, guide_for(stream_id)


@route(DEVICE, "GET", r"/device/neighbours/([\w\-]+)")
def dev_neighbours(h, item_id):
    h.device()
    prev_id, next_id = catalog.neighbours("live", item_id)
    return 200, {"prev": prev_id, "next": next_id}


@route(DEVICE, "GET", r"/device/favorite")
def dev_favorite_state(h):
    """Är titeln en favorit? (spelarens meny visar Lägg till eller Ta bort)."""
    dev, scope = h.context()
    kind = (h.query.get("type") or [""])[0]
    item_id = (h.query.get("id") or [""])[0]
    if scope.private or not catalog.in_scope(kind, item_id, scope):
        return 200, {"favorite": False}
    return 200, {"favorite": (kind, str(item_id)) in catalog.favorite_keys(scope.pid)}


@route(DEVICE, "POST", r"/device/favorites")
def dev_favorite(h):
    dev, scope = h.context()
    data = h.read_json()
    if data.get("type") == "episode":
        on = extras.set_episode_favorite(scope, data.get("id"), data.get("series_id"), data.get("name"),
                                         data.get("ext"), data.get("icon"), bool(data.get("on", True)))
        return 200, {"favorite": on}
    if not catalog.in_scope(data.get("type"), data.get("id"), scope):
        raise HttpError(404, "Finns inte.")
    if scope.guest:
        raise HttpError(400, "Gästprofilen sparar inga favoriter.")
    on = bool(data.get("on", True))
    if data.get("toggle"):
        # Favoritknappen på fjärrkontrollen: växla.
        on = (data.get("type"), str(data.get("id"))) not in catalog.favorite_keys(scope.pid)
    on = catalog.set_favorite(data.get("type"), data.get("id"), on, scope.pid)
    return 200, {"favorite": on}


@route(DEVICE, "POST", r"/device/filters")
def dev_filter_create(h):
    dev, scope = h.context()
    if scope.group_id is not None:
        raise HttpError(403, "Inte tillåtet för den här boxen.")
    data = h.read_json()
    fid = extras.create_filter(data.get("name"), data.get("type"), data.get("params") or {}, scope.adult)
    return 201, {"id": fid}


@route(DEVICE, "DELETE", r"/device/filters/(\d+)")
def dev_filter_delete(h, fid):
    dev, scope = h.context()
    if not extras.delete_filter(int(fid), scope.adult or 0):
        raise HttpError(404, "Finns inte.")
    return 200, {"ok": True}


@route(DEVICE, "POST", r"/device/play")
def dev_play(h):
    dev, scope = h.context()
    data = h.read_json()
    result = devices.request_play(
        dev, data.get("type"), data.get("id"), ext=data.get("ext"), force=bool(data.get("force")),
        exclude_portals=data.get("exclude_portals") or [], name=data.get("name"), scope=scope,
        series_id=data.get("series_id"), start=data.get("start"), duration=data.get("duration"),
        icon=data.get("icon"), recording_id=data.get("recording_id"), viewer_id=scope.pid,
    )
    if data.get("failover_from") and result.get("status") == "ok":
        db.log_event("failover", {"from_portal": data.get("failover_from"), "to": result["portal_name"],
                                  "reason": data.get("reason")}, device_id=dev["id"], session_id=result["session_id"])
    return 200, result


# ----- inspelningar -----
@route(DEVICE, "GET", r"/device/recordings")
def dev_recordings(h):
    """Utan all=1: det som syns i boxens nuvarande läge. Med all=1: hela schemat för inspelningstjänsten."""
    dev, scope = h.context()
    if h.query.get("all", ["0"])[0] == "1":
        rows = recordings.for_device(dev["id"])
        if not scope.adult:
            # Schemat behövs i sin helhet för inspelningstjänsten, men vuxentitlar och kanaler visas bara i vuxenläget.
            for r in rows:
                if r.get("adult"):
                    r["title"] = "Inspelning"
                    r["channel"] = None
        return 200, rows
    return 200, recordings.for_device(dev["id"], adult=scope.adult or 0)


@route(DEVICE, "POST", r"/device/recordings")
def dev_recording_create(h):
    dev, scope = h.context()
    data = h.read_json()
    kind = data.get("kind") or "live"
    if kind == "series":
        sid = str(data.get("series_id") or "")
        if not catalog.in_scope("series", sid, scope):
            raise HttpError(404, "Serien finns inte.")
        return 201, recordings.create_series(dev, scope, sid, series_detail(sid), data.get("season"))
    if kind in recordings.MEDIA_SOURCES:
        rec, created = recordings.create_media(dev, scope, kind, data.get("id"), data.get("title"), data.get("ext"),
                                               data.get("series_id"))
        return (201 if created else 200), rec
    rec, created = recordings.create(dev, scope, data.get("stream_id"), data.get("title"),
                                     data.get("start"), data.get("end"))
    return (201 if created else 200), rec


@route(DEVICE, "POST", r"/device/recordings/(\d+)/status")
def dev_recording_status(h, rid):
    dev = h.device()
    data = h.read_json()
    return 200, recordings.report(dev, int(rid), data.get("status"), data.get("size_bytes"),
                                  data.get("file"), data.get("error"))


@route(DEVICE, "DELETE", r"/device/recordings/(\d+)")
def dev_recording_delete(h, rid):
    dev = h.device()
    recordings.remove_by_device(dev, int(rid))
    return 200, {"ok": True}


@route(DEVICE, "GET", r"/device/app-update")
def dev_app_update(h):
    h.device()
    current = _int_arg(h.query, "code", 0, 0, 10**9)
    # force=1 när man själv väljer "Sök efter uppdatering": fråga GitHub direkt (högst var 20:e sekund).
    latest = updates.latest(force=(h.query.get("force") or ["0"])[0] == "1")
    available = bool(latest and latest["version_code"] > current)
    return 200, {"available": available, "latest": latest, "current": current}


@route(DEVICE, "GET", r"/device/app.apk")
def dev_app_apk(h):
    h.device()
    latest = updates.latest()
    if not latest:
        raise HttpError(404, "Ingen version hittades.")
    try:
        path = updates.ensure_apk(latest["version_code"])
    except Exception as exc:
        raise HttpError(502, f"Kunde inte hämta appen från GitHub: {exc}")
    size = os.path.getsize(path)
    h.send_response(200)
    h.send_header("Content-Type", "application/vnd.android.package-archive")
    h.send_header("Content-Length", str(size))
    h.end_headers()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(256 * 1024)
            if not chunk:
                break
            h.wfile.write(chunk)
    return None


@route(DEVICE, "POST", r"/device/heartbeat")
def dev_heartbeat(h):
    dev = h.device()
    data = h.read_json()
    return 200, devices.heartbeat(dev, str(data.get("session_id")), data.get("metrics"))


@route(DEVICE, "POST", r"/device/stop")
def dev_stop(h):
    dev = h.device()
    data = h.read_json()
    devices.end_session(dev, str(data.get("session_id")), data.get("reason", "stopped"), data.get("metrics"))
    return 200, {"ok": True}


@route(DEVICE, "POST", r"/device/report")
def dev_report(h):
    dev = h.device()
    data = h.read_json()
    devices.report(dev, data.get("kind"), data.get("session_id"), data.get("detail") or {})
    return 200, {"ok": True}


_SPEED_CHUNK = os.urandom(256 * 1024)


@route(DEVICE, "GET", r"/device/speedtest")
def dev_speedtest(h):
    h.device()
    size = _int_arg(h.query, "mb", 20, 1, 100) * 1024 * 1024
    h.send_response(200)
    h.send_header("Content-Type", "application/octet-stream")
    h.send_header("Content-Length", str(size))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    sent = 0
    while sent < size:
        chunk = _SPEED_CHUNK[: min(len(_SPEED_CHUNK), size - sent)]
        h.wfile.write(chunk)
        sent += len(chunk)
    return None


@route(DEVICE, "GET", r"/device/summary")
def dev_summary(h):
    """Kompakt status för Home Assistant-sensorer."""
    h.device()
    diag = diagnostics.overview()
    items, best = portals.summaries()
    active = next((p for p in items if p["active"]), None)
    sessions = devices.active_sessions()
    worst = diag["findings"][0] if diag["findings"] else None
    account = diagnostics._account()
    return 200, {
        "level": diag["level"],
        "headline": worst["title"] if worst else "OK",
        "detail": worst["detail"] if worst else "",
        "active_portal": active["name"] if active else None,
        "api_ms": active["last_check"]["api_ms"] if active and active["last_check"] else None,
        "portals_ok": sum(1 for p in items if p["ok"]),
        "portals_total": len(items),
        "playing": [{"device": s["device_name"], "title": s["item_name"]} for s in sessions],
        "expires": account["exp_date"] if account else None,
        "active_cons": account["active_cons"] if account else None,
        "max_connections": account["max_connections"] if account else None,
        "next_match": _match_summary(),
    }


def _match_summary():
    """Mina sporter: nästa (eller pågående) sändning, för sensorn "TV Hub nästa match"."""
    try:
        e = sports.next_match()
    except Exception:  # noqa: BLE001 - sensorn ska fungera även utan programguide
        return None
    if not e:
        return None
    return {"title": e["title"], "start": e["start"], "end": e["end"], "channel": e["channel"],
            "live": e["state"] == "now", "interests": e["interests"], "when": e["when"]}


# ----- Mina sporter, När går …? och TV-guiden -----
@route(DEVICE, "GET", r"/device/sports")
def dev_sports(h):
    dev, scope = h.context()
    if scope.adult:
        return 200, {"now": [], "upcoming": [], "past": [], "interests": [], "status": {}}
    iid = h.query.get("interest", [None])[0]
    if iid:
        evs = sports.events(scope, int(iid))
        out = sports._split(evs)
        out["interests"] = []
        return 200, out
    return 200, sports.overview(scope)


@route(DEVICE, "GET", r"/device/when")
def dev_when(h):
    dev, scope = h.context()
    if scope.adult:
        return 200, {"now": [], "upcoming": [], "past": [], "library": [], "channels": [], "query": ""}
    return 200, sports.when(h.query.get("q", [""])[0], scope)


@route(DEVICE, "GET", r"/device/epg-grid")
def dev_epg_grid(h):
    """TV-guiden: program för några kanaler i ett tidsfönster. Från den sparade guiden när kanalen finns där,
    annars hämtas kanalens guide (som i programguiden)."""
    dev, scope = h.context()
    ids = [i for i in h.query.get("ids", [""])[0].split(",") if i][:30]
    now = int(time.time())
    t0 = _int_arg(h.query, "from", now - 3600, 0, 10**11)
    t1 = _int_arg(h.query, "to", now + 4 * 3600, 0, 10**11)
    out = []
    futures = {}
    for cid in ids:
        if not catalog.in_scope("live", cid, scope):
            continue
        item = catalog.get_item("live", cid) or {}
        key = sports._epg_key(item) if item else None
        scanned = key and db.q1("SELECT 1 FROM epg_scan WHERE epg_key = ? AND error IS NULL", (key,))
        if scanned:
            rows = db.q("SELECT start, end, title, description FROM epg WHERE epg_key = ? AND end > ? AND start < ? ORDER BY start",
                        (key, t0, t1))
            progs = [dict(r) for r in rows]
            out.append({"id": cid, "archive_days": int(item.get("archive_days") or 0), "programs": progs})
        else:
            futures[cid] = _epg_pool.submit(guide_for, cid)
            out.append({"id": cid, "archive_days": int(item.get("archive_days") or 0), "programs": None})
    for ch in out:
        if ch["programs"] is None:
            try:
                g = futures[ch["id"]].result(timeout=20)
                ch["programs"] = [{"start": p["start"], "end": p["end"], "title": p["title"], "description": p["description"]}
                                  for p in g["programs"] if p["end"] > t0 and p["start"] < t1]
            except Exception:  # noqa: BLE001 - kanalen visas utan program
                ch["programs"] = []
    return 200, {"from": t0, "to": t1, "now": now, "channels": out}


@route(ADMIN, "GET", r"/api/sports")
def admin_sports(h):
    evs = sports.events()
    items = []
    for i in sports.interests():
        mine = [e for e in evs if i["name"] in e["interests"]]
        i["counts"] = {s: sum(1 for e in mine if e["state"] == s) for s in ("now", "upcoming", "past")}
        items.append(i)
    cats = [{"category_id": c["category_id"], "name": c["name"], "auto": bool(sports.AUTO_CATEGORY.search(c["name"] or "")) and not c["hidden"]}
            for c in catalog.categories("live") if not c["adult"]]
    return 200, {"interests": items, "settings": sports.settings(), "status": sports.status(), "categories": cats,
                 "boxes": [{"id": b["id"], "name": b["name"]} for b in control.boxes()]}


@route(ADMIN, "POST", r"/api/sports")
def admin_sport_create(h):
    return 201, sports.create_interest(h.read_json())


@route(ADMIN, "PUT", r"/api/sports/(\d+)")
def admin_sport_update(h, iid):
    return 200, sports.update_interest(int(iid), h.read_json())


@route(ADMIN, "DELETE", r"/api/sports/(\d+)")
def admin_sport_delete(h, iid):
    if not sports.delete_interest(int(iid)):
        raise HttpError(404, "Finns inte.")
    return 200, {"ok": True}


@route(ADMIN, "GET", r"/api/sports/preview/(\d+)")
def admin_sport_preview(h, iid):
    if not sports.get_interest(int(iid)):
        raise HttpError(404, "Finns inte.")
    return 200, sports._split(sports.events(interest_id=int(iid)), limit=15)


@route(ADMIN, "PUT", r"/api/sports/settings")
def admin_sport_settings(h):
    out = sports.save_settings(h.read_json())
    return 200, out


@route(ADMIN, "POST", r"/api/sports/scan")
def admin_sport_scan(h):
    if sports.status()["running"]:
        return 200, {"started": False, "message": "Genomsökningen pågår redan."}
    sports.request_scan()
    return 200, {"started": True}


@route(ADMIN, "GET", r"/api/when")
def admin_when(h):
    return 200, sports.when(h.query.get("q", [""])[0])


# ----- Fjärrstart och röstkommandon -----
@route(DEVICE, "GET", r"/device/commands")
def dev_commands(h):
    """Boxen väntar här (long-poll) på kommandon: spela, pausa, Filmkväll …"""
    dev = h.device()
    timeout = _int_arg(h.query, "wait", 25, 0, 55)
    return 200, {"commands": control.wait(dev["id"], timeout)}


@route(DEVICE, "GET", r"/device/boxes")
def dev_boxes(h):
    dev = h.device()
    return 200, control.boxes(exclude=dev["id"])


@route(DEVICE, "POST", r"/device/send")
def dev_send(h):
    """Telefonen: spela en titel på en box ("Spela på TV:n")."""
    dev, scope = h.context()
    data = h.read_json()
    try:
        return 200, control.send_item(data.get("device_id"), data)
    except control.ControlError as exc:
        raise HttpError(400, str(exc))


@route(ADMIN, "GET", r"/api/voice")
def admin_voice(h):
    return 200, {
        "commands": control.commands(),
        "actions": control.ACTIONS,
        "boxes": [dict(b, room=control.spoken_room(b["name"])) for b in control.boxes()],
        "listener": control.listener_status(),
    }


@route(ADMIN, "GET", r"/api/voice/media-players")
def admin_voice_players(h):
    return 200, control.media_players()


@route(ADMIN, "POST", r"/api/voice")
def admin_voice_create(h):
    return 201, control.save_command(h.read_json())


@route(ADMIN, "PUT", r"/api/voice/(\d+)")
def admin_voice_update(h, cid):
    return 200, control.save_command(h.read_json(), int(cid))


@route(ADMIN, "DELETE", r"/api/voice/(\d+)")
def admin_voice_delete(h, cid):
    control.delete_command(int(cid))
    return 200, {"ok": True}


@route(ADMIN, "POST", r"/api/voice/(\d+)/run")
def admin_voice_run(h, cid):
    try:
        return 200, control.run_command(int(cid))
    except control.ControlError as exc:
        raise HttpError(400, str(exc))


@route(ADMIN, "POST", r"/api/voice/(\d+)/script")
def admin_voice_script(h, cid):
    status = control.sync_script(int(cid))
    cmd = control.command(int(cid))
    return 200, {"status": status, "yaml": control.script_yaml(cmd)}


@route(ADMIN, "POST", r"/api/voice/defaults")
def admin_voice_defaults(h):
    data = h.read_json()
    return 200, {"created": control.add_defaults(data.get("device_id"), data.get("room"))}


@route(ADMIN, "POST", r"/api/play-on")
def admin_play_on(h):
    """Webbadminen: spela en titel på en box."""
    data = h.read_json()
    try:
        return 200, control.send_item(data.get("device_id"), data)
    except control.ControlError as exc:
        raise HttpError(400, str(exc))


# ----- Nytt tillagt (med datum) -----
@route(DEVICE, "GET", r"/device/new")
def dev_new(h):
    """Titlar som dykt upp i listorna, nyaste först, med TV Hubs datum (first_seen)."""
    dev, scope = h.context()
    kind = (h.query.get("type") or ["all"])[0]
    limit = _int_arg(h.query, "limit", 150, 1, 500)
    kinds = catalog.TYPES if kind == "all" else [kind]
    if any(k not in catalog.TYPES for k in kinds):
        raise HttpError(400, "Okänd typ")
    params = {"sort": "added", "new_only": "1"}
    day = _int_arg(h.query, "day", 0, 0, 10**11)
    if day:
        # Nytt per dag: bara den dagen (start = midnatt, från /device/new-summary).
        params.update(seen_from=str(day), seen_to=str(day + 86400 + 3600))
    category = (h.query.get("category_id") or [None])[0]
    items = []
    for k in kinds:
        found, _ = catalog.browse(k, scope, category, params, 0, limit)
        items += found
    if day:
        items = [i for i in items if catalog.day_start(i.get("first_seen") or 0) == day]
    items.sort(key=lambda i: -(i.get("first_seen") or 0))
    return 200, {"items": items[:limit]}


@route(DEVICE, "GET", r"/device/channel-by-num")
def dev_channel_by_num(h):
    """Sifferknapparna på fjärrkontrollen: kanalen med det numret (samma nummer som i kanallistan)."""
    dev, scope = h.context()
    num = _int_arg(h.query, "num", 0, 0, 10_000_000)
    if num <= 0:
        raise HttpError(400, "Ange ett kanalnummer")
    found, _ = catalog.browse("live", scope, None, {"num": num}, 0, 1)
    if not found:
        raise HttpError(404, f"Ingen kanal har nummer {num}.")
    return 200, found[0]


@route(DEVICE, "GET", r"/device/new-summary")
def dev_new_summary(h):
    """Nytt per dag: kategorier med nya titlar och antal, per dag (senaste 14 dagarna)."""
    dev, scope = h.context()
    return 200, {"days": catalog.new_summary(scope, _int_arg(h.query, "days", 14, 1, 60))}


# ----- Profiler och Mina program -----
@route(DEVICE, "GET", r"/device/profiles")
def dev_profiles(h):
    h.device()
    return 200, {"profiles": profiles.all_profiles(), "active": h.profile()}


_profile_limit = limits.AttemptLimiter(5, 300)  # fem försök per box och profil, sedan fem minuters paus


@route(DEVICE, "POST", r"/device/profile-check")
def dev_profile_check(h):
    """Byte till en profil med PIN: appen kontrollerar koden här."""
    dev = h.device()
    data = h.read_json()
    key = (dev["id"], str(data.get("profile_id")))
    wait = _profile_limit.reserve(key)
    if wait is not None:
        raise HttpError(429, f"För många försök. Vänta {wait // 60 + 1} min.")
    if not profiles.check_pin(data.get("profile_id"), data.get("pin")):
        time.sleep(1)
        raise HttpError(403, "Fel kod.")
    _profile_limit.success(key)
    return 200, {"ok": True, "profile": profiles.get(data.get("profile_id"))}


@route(DEVICE, "GET", r"/device/my-programs")
def dev_my_programs(h):
    """Snabbvalen för profilen som tittar, med vad som spelas just nu (senaste Rapport osv.)."""
    dev, scope = h.context()
    if scope.adult:
        return 200, {"programs": []}
    rows = myprograms.for_profile(scope.pid)
    return 200, {"programs": [myprograms.resolve(r, scope.pid) for r in rows]}


@route(ADMIN, "GET", r"/api/profiles")
def admin_profiles(h):
    out = []
    for p in profiles.all_profiles():
        p = dict(p)
        p["programs"] = myprograms.for_profile(p["id"])
        p["favorites"] = db.q1("SELECT COUNT(*) AS n FROM favorites WHERE profile_id = ?", (p["id"],))["n"]
        out.append(p)
    return 200, {"profiles": out, "modes": myprograms.MODES}


@route(ADMIN, "POST", r"/api/profiles")
def admin_profile_create(h):
    p = profiles.create(h.read_json())
    taste.invalidate()
    return 201, p


@route(ADMIN, "PUT", r"/api/profiles/(\d+)")
def admin_profile_update(h, pid):
    p = profiles.update(int(pid), h.read_json())
    taste.invalidate()
    return 200, p


@route(ADMIN, "DELETE", r"/api/profiles/(\d+)")
def admin_profile_delete(h, pid):
    profiles.delete(int(pid))
    taste.invalidate()
    return 200, {"ok": True}


@route(ADMIN, "POST", r"/api/my-programs")
def admin_program_create(h):
    return 201, myprograms.create(h.read_json())


@route(ADMIN, "PUT", r"/api/my-programs/(\d+)")
def admin_program_update(h, pid):
    return 200, myprograms.update(int(pid), h.read_json())


@route(ADMIN, "DELETE", r"/api/my-programs/(\d+)")
def admin_program_delete(h, pid):
    if not myprograms.delete(int(pid)):
        raise HttpError(404, "Finns inte.")
    return 200, {"ok": True}


@route(ADMIN, "POST", r"/api/my-programs/suggest")
def admin_program_suggest(h):
    data = h.read_json()
    if not profiles.get(data.get("profile_id")):
        raise HttpError(404, "Profilen finns inte.")
    return 200, {"created": myprograms.suggest(int(data["profile_id"]))}


@route(ADMIN, "GET", r"/api/my-programs/preview/(\d+)")
def admin_program_preview(h, pid):
    row = db.q1("SELECT * FROM my_programs WHERE id = ?", (int(pid),))
    if not row:
        raise HttpError(404, "Finns inte.")
    return 200, myprograms.resolve(myprograms._row(row), row["profile_id"])


# ----- Vuxenläge med PIN-kod -----
@route(DEVICE, "POST", r"/device/adult-unlock")
def dev_adult_unlock(h):
    """Utan pin: talar om ifall kod krävs. Med pin: nyckel som appen skickar som X-TVHub-Unlock."""
    dev = h.device()
    data = h.read_json()
    if not h.profile().get("allow_adult"):
        raise HttpError(403, "Byt till en profil som får använda vuxenläget först.")
    result = devices.unlock_adult(dev, data.get("pin"))
    if result["error"]:
        raise HttpError(403, result["error"])
    return 200, result


@route(DEVICE, "POST", r"/device/adult-lock")
def dev_adult_lock(h):
    dev = h.device()
    devices.lock_adult(dev["id"])
    return 200, {"ok": True}


@route(ADMIN, "GET", r"/api/adult-pin")
def admin_adult_pin(h):
    return 200, {"set": devices.adult_pin_set()}


@route(ADMIN, "PUT", r"/api/adult-pin")
def admin_adult_pin_set(h):
    return 200, {"set": devices.set_adult_pin(h.read_json().get("pin"))}


# ----- Fortsätt titta: ta bort -----
@route(DEVICE, "POST", r"/device/continue/remove")
def dev_continue_remove(h):
    dev, scope = h.context()
    data = h.read_json()
    return 200, {"removed": extras.remove_progress(scope, data.get("type"), data.get("id"), data.get("series_id"))}


# ----- Påminnelser -----
@route(DEVICE, "GET", r"/device/reminders")
def dev_reminders(h):
    dev, scope = h.context()
    return 200, reminders.for_device(dev["id"], scope.adult)


@route(DEVICE, "POST", r"/device/reminders")
def dev_reminder_create(h):
    dev, scope = h.context()
    data = h.read_json()
    rem, created = reminders.create(dev, scope, data.get("stream_id"), data.get("title"), data.get("start"), data.get("end"))
    rem["phone_notify"] = bool(reminders.settings().get("notify")) and not rem["adult"]
    return (201 if created else 200), rem


@route(DEVICE, "DELETE", r"/device/reminders/(\d+)")
def dev_reminder_delete(h, rid):
    dev = h.device()
    if not reminders.delete(dev, rid):
        raise HttpError(404, "Påminnelsen finns inte.")
    return 200, {"ok": True}


@route(ADMIN, "GET", r"/api/reminders/settings")
def admin_reminder_settings(h):
    return 200, reminders.settings()


@route(ADMIN, "PUT", r"/api/reminders/settings")
def admin_reminder_settings_save(h):
    return 200, reminders.save_settings(h.read_json())


@route(ADMIN, "GET", r"/api/reminders/notify-services")
def admin_notify_services(h):
    return 200, reminders.notify_services()


@route(ADMIN, "POST", r"/api/reminders/test")
def admin_reminder_test(h):
    return 200, reminders.test_notify()


# ----- Filmkväll -----
NIGHT_PARAMS = ("anim", "max", "new", "high", "swe", "kids")


def _night_query(h):
    params = {k: h.query[k][0] for k in NIGHT_PARAMS if h.query.get(k)}
    kind = (h.query.get("kind") or ["vod"])[0]
    exclude = [x for x in (h.query.get("exclude") or [""])[0].split(",") if x][:200]
    limit = _int_arg(h.query, "limit", 10, 1, 30)
    return params, kind, exclude, limit


def _viewers_public():
    return [{"id": v["id"], "name": v["name"], "rules": v["rules"]} for v in taste.viewers()]


@route(DEVICE, "GET", r"/device/night")
def dev_night(h):
    dev, scope = h.context()
    if scope.adult:
        raise HttpError(400, "Filmkväll finns bara i vanligt läge.")
    params, kind, exclude, limit = _night_query(h)
    viewer = taste.resolve_viewer((h.query.get("viewer") or [""])[0]) or h.viewer_id() or taste.default_viewer_id()
    result = taste.suggest(viewer, kind, params, limit, exclude)
    result["viewers"] = _viewers_public()
    return 200, result


@route(DEVICE, "POST", r"/device/taste")
def dev_taste(h):
    """Tummar och snabbval från appen: rate, seen, tonight, never, wish, unwish."""
    dev, scope = h.context()
    if scope.adult:
        raise HttpError(400, "Finns bara i vanligt läge.")
    data = h.read_json()
    kind, item_id, action = data.get("type") or "vod", str(data.get("id") or ""), data.get("action")
    viewer = taste.resolve_viewer(data.get("viewer")) or h.viewer_id() or taste.default_viewer_id()
    if action == "rate":
        return 200, taste.rate(viewer, kind, item_id, data.get("value"), bool(data.get("seen")), dev["id"])
    if action == "seen":
        taste.log_seen(viewer, kind, item_id, "manual", dev["id"], claim=True)
    elif action in ("tonight", "never"):
        taste.hide(viewer, kind, item_id, forever=action == "never")
    elif action in ("wish", "unwish"):
        taste.set_wish(kind, item_id, action == "wish", None)
    else:
        raise HttpError(400, "Okänt val.")
    return 200, {"ok": True}


@route(DEVICE, "GET", r"/device/viewers")
def dev_viewers(h):
    h.device()
    return 200, _viewers_public()


@route(ADMIN, "GET", r"/api/viewers")
def admin_viewers(h):
    return 200, taste.viewers()


@route(ADMIN, "POST", r"/api/viewers")
def admin_viewer_create(h):
    # Tittarlägen och profiler är samma sak: skapa via profilerna (namnkontroll), spara smak och regler här.
    data = h.read_json()
    p = profiles.create({"name": data.get("name")})
    rest = {k: v for k, v in data.items() if k != "name"}
    return 201, taste.save_viewer(rest, p["id"])


@route(ADMIN, "PUT", r"/api/viewers/(\d+)")
def admin_viewer_update(h, vid):
    data = h.read_json()
    if str(data.get("name") or "").strip():
        profiles.update(int(vid), {"name": data["name"]})
    rest = {k: v for k, v in data.items() if k != "name"}
    return 200, taste.save_viewer(rest, int(vid))


@route(ADMIN, "DELETE", r"/api/viewers/(\d+)")
def admin_viewer_delete(h, vid):
    profiles.delete(int(vid))
    taste.invalidate()
    return 200, {"ok": True}


@route(ADMIN, "GET", r"/api/night")
def admin_night(h):
    params, kind, exclude, limit = _night_query(h)
    viewer = taste.resolve_viewer((h.query.get("viewer") or [""])[0]) or taste.default_viewer_id()
    result = taste.suggest(viewer, kind, params, limit, exclude)
    result["ai_picks"] = ai.picks_info(viewer, kind)
    return 200, result


@route(ADMIN, "GET", r"/api/night/settings")
def admin_night_settings(h):
    return 200, {"version_pref": taste.version_pref()}


@route(ADMIN, "PUT", r"/api/night/settings")
def admin_night_settings_save(h):
    data = h.read_json()
    pref = data.get("version_pref")
    if pref not in ("swedish", "quality"):
        raise HttpError(400, "Okänt val.")
    db.set_setting("version_pref", pref)
    taste.invalidate_catalog()
    return 200, {"version_pref": pref}


@route(ADMIN, "GET", r"/api/seen")
def admin_seen(h):
    viewer = taste.resolve_viewer((h.query.get("viewer") or [""])[0])
    return 200, taste.seen_log(_int_arg(h.query, "limit", 200, 1, 2000), viewer)


@route(ADMIN, "DELETE", r"/api/seen/(\d+)")
def admin_seen_delete(h, rid):
    taste.delete_seen(int(rid))
    return 200, {"ok": True}


@route(ADMIN, "GET", r"/api/wishlist")
def admin_wishlist(h):
    return 200, taste.wishlist()


@route(ADMIN, "POST", r"/api/wishlist")
def admin_wishlist_set(h):
    data = h.read_json()
    return 200, {"on": taste.set_wish(data.get("type") or "vod", str(data.get("id") or ""), bool(data.get("on")),
                                      taste.resolve_viewer(data.get("viewer")))}


@route(ADMIN, "GET", r"/api/ratings")
def admin_ratings(h):
    return 200, taste.ratings_list(taste.resolve_viewer((h.query.get("viewer") or [""])[0]))


@route(ADMIN, "POST", r"/api/rate")
def admin_rate(h):
    data = h.read_json()
    viewer = taste.resolve_viewer(data.get("viewer"))
    if not viewer:
        raise HttpError(400, "Välj tittarläge.")
    kind, item_id = data.get("type") or "vod", str(data.get("id") or "")
    if data.get("action") == "seen":
        taste.log_seen(viewer, kind, item_id, "manual")
        return 200, {"ok": True}
    return 200, taste.rate(viewer, kind, item_id, data.get("value"), bool(data.get("seen")))


@route(ADMIN, "POST", r"/api/night-action")
def admin_night_action(h):
    data = h.read_json()
    viewer = taste.resolve_viewer(data.get("viewer"))
    if not viewer or data.get("action") not in ("tonight", "never"):
        raise HttpError(400, "Ogiltigt val.")
    taste.hide(viewer, data.get("type") or "vod", str(data.get("id") or ""), forever=data.get("action") == "never")
    return 200, {"ok": True}


@route(ADMIN, "GET", r"/api/taste/item")
def admin_taste_item(h):
    kind = (h.query.get("type") or ["vod"])[0]
    item_id = (h.query.get("id") or [""])[0]
    key = taste.key_for(kind, item_id)
    if not key:
        return 200, {"available": False}
    per = []
    for v in taste.viewers():
        seen = db.q1("SELECT MAX(at) AS at FROM seen_log WHERE viewer_id = ? AND type = ? AND work_key = ?",
                     (v["id"], kind, key))["at"]
        per.append({"id": v["id"], "name": v["name"], "rating": taste.rating_of(v["id"], kind, key), "seen": seen})
    return 200, {"available": True, "viewers": per, "wish": taste.in_wishlist(kind, key),
                 "versions": taste.versions_of(kind, item_id) if kind == "vod" else []}


@route(ADMIN, "POST", r"/api/taste/import")
def admin_taste_import(h):
    data = h.read_json()
    viewer = taste.resolve_viewer(data.get("viewer"))
    if not viewer:
        raise HttpError(400, "Välj tittarläge.")
    return 200, taste.import_csv(data.get("text"), viewer)


@route(ADMIN, "GET", r"/api/ai")
def admin_ai(h):
    return 200, ai.settings()


@route(ADMIN, "PUT", r"/api/ai")
def admin_ai_save(h):
    return 200, ai.save_settings(h.read_json())


@route(ADMIN, "POST", r"/api/ai/test")
def admin_ai_test(h):
    return 200, ai.test()


@route(ADMIN, "POST", r"/api/ai/run")
def admin_ai_run(h):
    if not ai.settings()["enabled"]:
        raise HttpError(400, "Välj en AI-tjänst först.")
    threading.Thread(target=lambda: _safe(ai.run_all, "manual"), daemon=True).start()
    return 200, {"started": True}


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception as exc:
        print(f"[fel] {fn.__name__}: {exc!r}", flush=True)


def _ai_viewer(data):
    viewer = taste.resolve_viewer(data.get("viewer"))
    if not viewer:
        raise HttpError(400, "Välj tittarläge.")
    kind = data.get("type") or "vod"
    return viewer, kind if kind in taste.KINDS else "vod"


@route(ADMIN, "POST", r"/api/ai/ask")
def admin_ai_ask(h):
    data = h.read_json()
    viewer, kind = _ai_viewer(data)
    try:
        return 200, ai.ask(viewer, kind, data.get("question"))
    except ai.AiError as exc:
        raise HttpError(502, str(exc))


@route(ADMIN, "GET", r"/api/ai/export")
def admin_ai_export(h):
    viewer, kind = _ai_viewer({"viewer": (h.query.get("viewer") or [""])[0], "type": (h.query.get("type") or ["vod"])[0]})
    return 200, {"text": ai.export_text(viewer, kind)}


@route(ADMIN, "POST", r"/api/ai/paste")
def admin_ai_paste(h):
    data = h.read_json()
    viewer, kind = _ai_viewer(data)
    return 200, ai.paste(viewer, kind, data.get("text"))


@route(ADMIN, "POST", r"/api/ai/use")
def admin_ai_use(h):
    """Chattens förslag blir Filmkvällens lista i ett dygn."""
    data = h.read_json()
    viewer, kind = _ai_viewer(data)
    picks = [p for p in (data.get("picks") or []) if isinstance(p, dict) and p.get("id")][:30]
    if not picks:
        raise HttpError(400, "Inga förslag.")
    ai.store(viewer, kind, [{"id": str(p["id"]), "reason": str(p.get("reason") or "")[:120]} for p in picks], "chat")
    return 200, {"count": len(picks)}


@route(ADMIN, "DELETE", r"/api/ai/picks")
def admin_ai_clear(h):
    viewer, kind = _ai_viewer({"viewer": (h.query.get("viewer") or [""])[0], "type": (h.query.get("type") or ["vod"])[0]})
    ai.clear(viewer, kind)
    return 200, {"ok": True}


@route(DEVICE, "GET", r"/device/remote")
def dev_remote(h):
    """Uppgifter för att nå TV Hub via Nabu Casa. Bara hemma, aldrig via Nabu Casa."""
    dev = h.device()
    if h.via_ingress:
        raise HttpError(404, "Hittades inte")
    if not dev["remote_allowed"]:
        # Bara enheter som du har slagit på det för (telefonen) får Home Assistant-nyckeln.
        return 200, {"available": False, "missing": ["tillstånd i webbadminen (Boxar, Fjärråtkomst)"]}
    return 200, remote.device_config()


# Anrop som inte behövs (eller inte ska gå) via Nabu Casa.
NOT_VIA_INGRESS = {dev_pair, dev_speedtest, dev_remote, dev_commands}
DEVICE_VIA_INGRESS = [r for r in DEVICE if r[2] not in NOT_VIA_INGRESS]

DENIED_PAGE = """<!doctype html><html lang="sv"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TV Hub</title>
<body style="font-family:system-ui,sans-serif;background:#111;color:#eee;padding:32px;line-height:1.5">
<h2>TV Hub</h2><p>Webbadminen är bara för administratörer i Home Assistant.</p>
<p style="color:#999">Den här användaren används av TV Hub-appen på telefonen.</p></body></html>"""


class IngressHandler(DeviceHandler):
    """Port 8080. Webbadminen för administratörer, och appens anrop via Nabu Casa."""
    via_ingress = True

    def dispatch(self, method):
        path = urlparse(self.path).path
        self.routes = DEVICE_VIA_INGRESS if path.startswith("/device/") else ADMIN
        if self.routes is ADMIN and AdminHandler.allowed(self) and not remote.admin_allowed(self.headers, self.client_ip()):
            if method == "GET" and path in ("/", "/index.html"):
                body = DENIED_PAGE.encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(403, {"error": "Webbadminen är bara för administratörer i Home Assistant."})
            return
        super().dispatch(method)

    def allowed(self):
        return AdminHandler.allowed(self)

    def auth_header(self):
        token = (self.headers.get("X-TVHub-Token") or "").strip()
        return f"Bearer {token}" if token else None

    def device_ip(self):
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return f"Nabu Casa ({forwarded})" if forwarded else "Nabu Casa"


class BoundedServer(ThreadingHTTPServer):
    """En tråd per anslutning, men högst max_threads samtidigt. Fler får 503 i stället för att
    tillägget får slut på minne (långa förfrågningar för fjärrstart håller en tråd i upp till 55 s)."""
    max_threads = 96
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(self.max_threads)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nRetry-After: 5\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def start_servers():
    admin = BoundedServer(("0.0.0.0", CFG["admin_port"]), IngressHandler)
    device = BoundedServer(("0.0.0.0", CFG["device_port"]), DeviceHandler)
    admin.daemon_threads = device.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True, name="admin").start()
    threading.Thread(target=device.serve_forever, daemon=True, name="device").start()
    return admin, device
