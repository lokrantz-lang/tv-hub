"""HTTP-servrar.

Admin (port 8080): bara via Home Assistant Ingress. Webbadmin + admin-API.
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

from . import VERSION, catalog, db, devices, diagnostics, portals
from .config import CFG
from .xtream import XtreamError

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MAX_BODY = 256 * 1024


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
def admin_device_rename(h, did):
    devices.rename(int(did), h.read_json().get("name"))
    return 200, {"ok": True}


@route(ADMIN, "DELETE", r"/api/devices/(\d+)")
def admin_device_delete(h, did):
    if not devices.remove(int(did)):
        raise HttpError(404, "Enheten finns inte")
    return 200, {"ok": True}


# ----- diagnostik -----
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

    def device(self):
        dev = devices.authenticate(self.headers.get("Authorization"), self.client_ip())
        if dev is None:
            raise HttpError(401, "Boxen är inte parad. Para om den i appen.")
        return dev


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
    dev = h.device()
    return 200, {
        "device": {"id": dev["id"], "name": dev["name"]},
        "favorites": catalog.favorites(),
        "groups": catalog.groups(with_items=True),
        "categories": {kind: catalog.categories(kind) for kind in catalog.TYPES},
        "recent_vod": catalog.recent("vod", 40),
        "counts": catalog.counts(),
    }


@route(DEVICE, "GET", r"/device/items")
def dev_items(h):
    h.device()
    kind = h.query.get("type", ["live"])[0]
    cid = h.query.get("category_id", [None])[0]
    if kind not in catalog.TYPES or cid is None:
        raise HttpError(400, "type och category_id krävs")
    return 200, catalog.items_in_category(kind, cid, _int_arg(h.query, "offset", 0),
                                          _int_arg(h.query, "limit", 500, 1, 2000))


@route(DEVICE, "GET", r"/device/search")
def dev_search(h):
    h.device()
    return 200, catalog.search(h.query.get("q", [""])[0], h.query.get("type", [None])[0],
                               _int_arg(h.query, "limit", 80, 1, 300))


@route(DEVICE, "GET", r"/device/series/([\w\-]+)")
def dev_series(h, sid):
    h.device()
    return 200, series_detail(sid)


@route(DEVICE, "GET", r"/device/epg/([\w\-]+)")
def dev_epg(h, stream_id):
    h.device()
    return 200, epg(stream_id)


@route(DEVICE, "GET", r"/device/neighbours/([\w\-]+)")
def dev_neighbours(h, item_id):
    h.device()
    prev_id, next_id = catalog.neighbours("live", item_id)
    return 200, {"prev": prev_id, "next": next_id}


@route(DEVICE, "POST", r"/device/favorites")
def dev_favorite(h):
    h.device()
    data = h.read_json()
    on = catalog.set_favorite(data.get("type"), data.get("id"), bool(data.get("on", True)))
    return 200, {"favorite": on}


@route(DEVICE, "POST", r"/device/play")
def dev_play(h):
    dev = h.device()
    data = h.read_json()
    result = devices.request_play(
        dev, data.get("type"), data.get("id"), ext=data.get("ext"), force=bool(data.get("force")),
        exclude_portals=data.get("exclude_portals") or [], name=data.get("name"),
    )
    if data.get("failover_from") and result.get("status") == "ok":
        db.log_event("failover", {"from_portal": data.get("failover_from"), "to": result["portal_name"],
                                  "reason": data.get("reason")}, device_id=dev["id"], session_id=result["session_id"])
    return 200, result


@route(DEVICE, "POST", r"/device/heartbeat")
def dev_heartbeat(h):
    dev = h.device()
    data = h.read_json()
    return 200, devices.heartbeat(dev, str(data.get("session_id")), data.get("metrics"))


@route(DEVICE, "POST", r"/device/stop")
def dev_stop(h):
    dev = h.device()
    data = h.read_json()
    devices.end_session(dev, str(data.get("session_id")), data.get("reason", "stopped"))
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


def start_servers():
    admin = ThreadingHTTPServer(("0.0.0.0", CFG["admin_port"]), AdminHandler)
    device = ThreadingHTTPServer(("0.0.0.0", CFG["device_port"]), DeviceHandler)
    admin.daemon_threads = device.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True, name="admin").start()
    threading.Thread(target=device.serve_forever, daemon=True, name="device").start()
    return admin, device
