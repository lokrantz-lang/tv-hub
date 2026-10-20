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

from . import VERSION, ai, catalog, db, devices, diagnostics, extras, info, portals, recordings, remote, taste, updates
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
        length = int(self.headers.get("Content-Length") or 0)
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
            req = urllib.request.Request(url, headers={"User-Agent": CFG["user_agent"]})
            with urllib.request.urlopen(req, timeout=8) as res:
                ctype = (res.headers.get("Content-Type") or "").split(";")[0].strip()
                if not ctype.startswith("image/") or ctype == "image/svg+xml":
                    raise HttpError(415, "Inte en bild")
                body = res.read(IMG_MAX_BYTES + 1)
        except HttpError:
            raise
        except Exception:
            raise HttpError(502, "Bilden kunde inte hämtas")
        if len(body) > IMG_MAX_BYTES:
            raise HttpError(413, "För stor bild")
        try:
            with open(path, "wb") as fh:
                fh.write(body)
            names = os.listdir(IMG_DIR)
            if len(names) > IMG_MAX_FILES:
                full = sorted((os.path.join(IMG_DIR, n) for n in names), key=os.path.getmtime)
                for old in full[: len(full) - IMG_MAX_FILES + 200]:
                    os.remove(old)
        except OSError:
            pass
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
        dev = devices.authenticate(self.auth_header(), self.device_ip())
        if dev is None:
            raise HttpError(401, "Boxen är inte parad. Para om den i appen.")
        if self.via_ingress:
            remote.mark_remote(dev, self.headers, self.device_ip())
        return dev

    def wants_adult(self):
        """Läget skickas av appen i varje anrop. Allt annat än "adult" betyder vanligt läge."""
        return (self.headers.get("X-TVHub-Mode") or "").strip().lower() == "adult"

    def wants_history(self):
        """Vuxenläget: "show" visar favoriter och historik, annars döljs de. Sparas gör de alltid."""
        return (self.headers.get("X-TVHub-History") or "").strip().lower() == "show"

    def context(self):
        dev = self.device()
        return dev, devices.scope_for(dev, self.wants_adult(), self.wants_history())

    def viewer_id(self):
        """Tittarläget som valts i Filmkväll (X-TVHub-Viewer). None = inget valt."""
        return taste.resolve_viewer((self.headers.get("X-TVHub-Viewer") or "").strip())


@route(DEVICE, "GET", r"/device/ping")
def dev_ping(h):
    return 200, {"app": "tvhub", "version": VERSION}


@route(DEVICE, "POST", r"/device/pair")
def dev_pair(h):
    data = h.read_json()
    result = devices.pair(data.get("code"), data.get("name"), data.get("model"),
                          data.get("app_version"), h.client_ip())
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
        "recent_vod": [] if group_only else catalog.recent("vod", 40, scope),
        # Rader för den nya hemskärmen
        "recent_series": [] if group_only else catalog.recent("series", 30, scope),
        "top_vod": [] if group_only else catalog.browse(
            "vod", scope, None, {"sort": "rating", "min_rating": "7.5", "min_year": str(time.gmtime().tm_year - 6)}, 0, 30)[0],
        "counts": catalog.counts(scope),
        "recordings": len(recordings.for_device(dev["id"], adult=scope.adult or 0)),
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
            item["watched"] = extras.is_watched(scope.adult or 0, kind, item["id"]) \
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
    item["resume"] = None if scope.private else extras.resume_position(adult, "vod", vod_id)
    item["watched"] = False if scope.private else extras.is_watched(adult, "vod", vod_id)
    if scope.private:
        item["favorite"] = False
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
    progress, watched = ({}, []) if scope.private else extras.series_progress(scope.adult or 0, sid)
    detail["progress"] = progress
    detail["watched"] = watched
    detail["favorite_episodes"] = [] if scope.private else extras.episode_favorite_ids(scope.adult, sid)
    if scope.private:
        detail["favorite"] = False
    if not scope.adult:
        detail.update(taste.detail_extras("series", sid, h.viewer_id()))
    return 200, detail


@route(DEVICE, "GET", r"/device/epg/([\w\-]+)")
def dev_epg(h, stream_id):
    h.device()
    return 200, epg(stream_id)


@route(DEVICE, "GET", r"/device/epg-now")
def dev_epg_now(h):
    """Nu och nästa för upp till 40 kanaler i ett anrop (för kanallistan)."""
    h.device()
    ids = [i for i in (h.query.get("ids", [""])[0]).split(",") if re.match(r"^[\w\-]{1,40}$", i)][:40]
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
    for p in cached(("guide", str(stream_id)), 600, produce):
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
    on = catalog.set_favorite(data.get("type"), data.get("id"), bool(data.get("on", True)))
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
        icon=data.get("icon"), recording_id=data.get("recording_id"), viewer_id=h.viewer_id(),
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
        return 200, recordings.for_device(dev["id"])
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
    latest = updates.latest()
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
    }


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
    return 201, taste.save_viewer(h.read_json())


@route(ADMIN, "PUT", r"/api/viewers/(\d+)")
def admin_viewer_update(h, vid):
    return 200, taste.save_viewer(h.read_json(), int(vid))


@route(ADMIN, "DELETE", r"/api/viewers/(\d+)")
def admin_viewer_delete(h, vid):
    taste.delete_viewer(int(vid))
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
    h.device()
    if h.via_ingress:
        raise HttpError(404, "Hittades inte")
    return 200, remote.device_config()


# Anrop som inte behövs (eller inte ska gå) via Nabu Casa.
NOT_VIA_INGRESS = {dev_pair, dev_speedtest, dev_remote}
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
        if self.routes is ADMIN and AdminHandler.allowed(self) and not remote.admin_allowed(self.headers):
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


def start_servers():
    admin = ThreadingHTTPServer(("0.0.0.0", CFG["admin_port"]), IngressHandler)
    device = ThreadingHTTPServer(("0.0.0.0", CFG["device_port"]), DeviceHandler)
    admin.daemon_threads = device.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True, name="admin").start()
    threading.Thread(target=device.serve_forever, daemon=True, name="device").start()
    return admin, device
