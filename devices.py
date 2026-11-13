"""Enheter (TV-boxar), parning, uppspelningssessioner och anslutningsvakt.

Toucan tillåter bara en ström åt gången. Vakten ser till att en box inte
startar en ström medan en annan box tittar, och att ett övertagande väntar
tills den andra boxen har stoppat.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from datetime import datetime, timezone

from . import catalog, db, extras, limits, portals, recordings, updates, xtream
from .config import CFG

ACTIVE_WINDOW_S = 35        # session räknas som aktiv om heartbeat inom detta
TAKEOVER_TIMEOUT_S = 15     # max väntan på att den andra boxen stoppar
PAIR_CODE_TTL_MIN = 10
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
SAFE_EXT = re.compile(r"^[A-Za-z0-9]{1,8}$")


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------- parning ----------
def new_pair_code():
    db.x("DELETE FROM pair_codes WHERE expires < ?", (db.iso(),))
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = db.later(minutes=PAIR_CODE_TTL_MIN)
    db.x("INSERT OR REPLACE INTO pair_codes (code, expires) VALUES (?, ?)", (code, expires))
    return {"code": code, "expires": expires}


# Gissning av parningskoder: högst 30 felaktiga försök på 10 minuter totalt (koden gäller 10 minuter).
_pair_limit = limits.AttemptLimiter(30, 600)


class TooManyAttempts(Exception):
    def __init__(self, seconds):
        super().__init__(f"För många försök. Vänta {max(1, seconds // 60 + 1)} min.")
        self.seconds = seconds


def pair(code, name, model, app_version, ip):
    """Förbrukar koden och skapar enheten i en och samma transaktion: samma kod kan bara ge en enhet."""
    code = str(code or "").strip()
    token = secrets.token_urlsafe(32)
    name = (str(name or "").strip() or "TV-box")[:40]
    with db.tx():
        used = db.x("DELETE FROM pair_codes WHERE code = ? AND expires >= ?", (code, db.iso())).rowcount
        if used != 1:
            wait = _pair_limit.reserve("pair")
            if wait is not None:
                raise TooManyAttempts(wait)
            return None
        device_id = db.x(
            """INSERT INTO devices (name, model, app_version, token_hash, created, last_seen, last_ip)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, str(model or "")[:60], str(app_version or "")[:20], _hash(token), db.iso(), db.iso(), ip),
        ).lastrowid
    db.log_event("paired", {"name": name, "model": model}, device_id=device_id)
    return {"token": token, "device_id": device_id, "name": name}


def create_token_device(name):
    """Skapar en enhet direkt (för Home Assistant-sensorer)."""
    token = secrets.token_urlsafe(32)
    device_id = db.x(
        "INSERT INTO devices (name, model, token_hash, created) VALUES (?, 'integration', ?, ?)",
        (name[:40], _hash(token), db.iso()),
    ).lastrowid
    return {"token": token, "device_id": device_id}


def authenticate(header, ip):
    if not header or not header.startswith("Bearer "):
        return None
    row = db.q1("SELECT * FROM devices WHERE token_hash = ?", (_hash(header[7:].strip()),))
    if row:
        db.x("UPDATE devices SET last_seen = ?, last_ip = ? WHERE id = ?", (db.iso(), ip, row["id"]))
    return row


def list_devices():
    out = []
    for row in db.q("""SELECT id, name, model, app_version, created, last_seen, last_ip, allow_adult, only_group_id,
                              pin_required, default_profile_id, revert_hours, adult_idle_minutes, remote_allowed
                       FROM devices ORDER BY id"""):
        item = dict(row)
        item["allow_adult"] = bool(item["allow_adult"])
        item["pin_required"] = bool(item["pin_required"])
        item["remote_allowed"] = bool(item["remote_allowed"])
        item["playing"] = active_session_for(row["id"])
        item["online"] = _seconds_since(row["last_seen"]) < ONLINE_S
        latest = updates.cached_latest()
        item["update_available"] = bool(latest and row["app_version"] and row["model"] != "integration"
                                        and _older(row["app_version"], latest.get("version_name")))
        out.append(item)
    return out


ONLINE_S = 600


def _seconds_since(stamp):
    if not stamp:
        return 10 ** 9
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return 10 ** 9
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def _older(have, latest):
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", str(v or ""))][:3]
    return bool(parts(latest)) and parts(have) < parts(latest)


def update_device(device_id, data):
    if not db.q1("SELECT 1 FROM devices WHERE id = ?", (device_id,)):
        raise LookupError("Boxen finns inte")
    if "name" in data:
        rename(device_id, data["name"])
    if "allow_adult" in data:
        db.x("UPDATE devices SET allow_adult = ? WHERE id = ?", (1 if data["allow_adult"] else 0, device_id))
    if "pin_required" in data:
        db.x("UPDATE devices SET pin_required = ? WHERE id = ?", (1 if data["pin_required"] else 0, device_id))
        with _pin_lock:
            _unlocks.pop(device_id, None)
    if "default_profile_id" in data:
        from . import profiles
        pid = data["default_profile_id"]
        pid = None if pid in (None, "", 0, "0") else int(pid)
        if pid is not None and not profiles.get(pid):
            raise LookupError("Profilen finns inte")
        db.x("UPDATE devices SET default_profile_id = ? WHERE id = ?", (pid, device_id))
    if "revert_hours" in data:
        hours = int(data["revert_hours"] or 0)
        if not 0 <= hours <= 48:
            raise ValueError("Ange 0–48 timmar.")
        db.x("UPDATE devices SET revert_hours = ? WHERE id = ?", (hours, device_id))
    if "remote_allowed" in data:
        db.x("UPDATE devices SET remote_allowed = ? WHERE id = ?", (1 if data["remote_allowed"] else 0, device_id))
    if "adult_idle_minutes" in data:
        minutes = int(data["adult_idle_minutes"] or 0)
        if not 1 <= minutes <= 180:
            raise ValueError("Ange 1–180 minuter.")
        db.x("UPDATE devices SET adult_idle_minutes = ? WHERE id = ?", (minutes, device_id))
    if "only_group_id" in data:
        group_id = data["only_group_id"]
        if group_id in (None, "", 0, "0"):
            group_id = None
        else:
            group_id = int(group_id)
            if not db.q1("SELECT 1 FROM groups WHERE id = ?", (group_id,)):
                raise LookupError("Gruppen finns inte")
        db.x("UPDATE devices SET only_group_id = ? WHERE id = ?", (group_id, device_id))


def scope_for(device, wants_adult, wants_history=True, profile=None):
    """Vad boxen får se: läget från appen, begränsat av boxens profil och vem som tittar.
    wants_history gäller bara vuxenläget (dolt val i appen)."""
    adult = 1 if wants_adult and device["allow_adult"] else 0
    return catalog.Scope(adult=adult, group_id=device["only_group_id"], hide_hidden=True,
                         history=bool(wants_history) or not adult,
                         profile_id=(profile or {}).get("id") or None, guest=bool((profile or {}).get("guest")))


def rename(device_id, name):
    name = (name or "").strip()[:40]
    if not name:
        raise ValueError("Namn saknas")
    db.x("UPDATE devices SET name = ? WHERE id = ?", (name, device_id))


def remove(device_id):
    db.x("UPDATE sessions SET ended = ?, end_reason = 'device_removed' WHERE device_id = ? AND ended IS NULL",
         (db.iso(), device_id))
    return db.x("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount > 0


# ---------- sessioner ----------
RECORD_TYPES = ("record", "record_archive", "record_vod")
RECORD_KIND = {"live": "live", "archive": "catchup", "vod": "vod", "episode": "episode"}  # källa → strömtyp


def _active_rows(exclude_device=None, watching_only=False):
    sql = """SELECT s.*, d.name AS device_name FROM sessions s JOIN devices d ON d.id = s.device_id
             WHERE s.ended IS NULL AND s.last_heartbeat >= ?"""
    args = [db.ago(seconds=ACTIVE_WINDOW_S)]
    if watching_only:
        sql += " AND s.type NOT IN ('record', 'record_archive', 'record_vod')"
    if exclude_device is not None:
        sql += " AND s.device_id != ?"
        args.append(exclude_device)
    return db.q(sql, args)


ADULT_LABEL = "18+"


def active_sessions():
    """Vuxensessioner visas aldrig med titel (webbadmin, Home Assistant, meddelanden till andra boxar)."""
    out = []
    for row in _active_rows():
        item = dict(row)
        item["metrics"] = json.loads(item["metrics"]) if item["metrics"] else None
        if item.get("adult"):
            item["item_name"] = ADULT_LABEL
            item["item_id"] = ""
        out.append(item)
    return out


def active_session_for(device_id):
    rows = [r for r in _active_rows(watching_only=True) if r["device_id"] == device_id]
    if not rows:
        return None
    r = rows[0]
    adult = bool(r["adult"])
    return {"session_id": r["id"], "type": r["type"], "item_id": "" if adult else r["item_id"],
            "item_name": ADULT_LABEL if adult else r["item_name"], "started": r["started"]}


def _end(session_id, reason):
    db.x("UPDATE sessions SET ended = ?, end_reason = ? WHERE id = ? AND ended IS NULL",
         (db.iso(), reason, session_id))


def _timeshift_start(portal, start_ts):
    """Catch-up-starttid i källans tidszon, format ÅÅÅÅ-MM-DD:TT-MM."""
    tz_name = db.get_setting(f"portal_tz:{portal['id']}")
    zone = timezone.utc
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(tz_name)
        except Exception:
            zone = timezone.utc
    return datetime.fromtimestamp(int(start_ts), zone).strftime("%Y-%m-%d:%H-%M")


_play_lock = threading.RLock()

# ---------- catch-up: vilken adressform källan godkänner ----------
TS_VARIANT = "timeshift_variant:{}"
TS_DISCOVERY_COOLDOWN_S = 600
TS_DISCOVERY_BUDGET_S = 10
TS_PROBE_TIMEOUT_S = 3
_ts_failed_at = {}   # portal_id -> när sökningen senast misslyckades


def _looks_like_video(status, ctype, body):
    """Ser svaret ut som en videoström? (Tom kropp, HTML, JSON och text är det inte.)"""
    if status not in (200, 206) or not body:
        return False
    ctype = (ctype or "").lower()
    if ctype.startswith("video/") or "mpegurl" in ctype:
        return True
    if len(body) > 188 and body[0] == 0x47 and body[188] == 0x47:
        return True
    if any(t in ctype for t in ("html", "json", "text", "xml")):
        return False
    return not all(b in (9, 10, 13) or 32 <= b < 127 for b in body[:64])


def _fetch_head(url, timeout):
    """(status, innehållstyp, första byten). status None = ingen kontakt."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": CFG["user_agent"], "Range": f"bytes=0-{PROBE_BYTES - 1}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type") or "", resp.read(PROBE_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.headers.get("Content-Type") or "", exc.read(PROBE_BYTES)
        except Exception:
            return exc.code, "", b""
    except Exception:
        return None, "", b""


def _discover_timeshift(portal, candidates):
    """Provar adressformerna i tur och ordning och sparar den första som ger video. Returnerar dess nyckel eller None.
    Resultatet loggas (utan adresser) som händelsen "timeshift" så att det syns i Diagnostik."""
    deadline = time.time() + TS_DISCOVERY_BUDGET_S
    results, found = [], None
    for key, url in candidates:
        if time.time() > deadline:
            break
        status, ctype, body = _fetch_head(url, TS_PROBE_TIMEOUT_S)
        ok = _looks_like_video(status, ctype, body)
        results.append({"variant": key, "status": status, "type": ctype[:40], "bytes": len(body), "video": ok})
        if ok:
            found = key
            break
    if found:
        db.set_setting(TS_VARIANT.format(portal["id"]), found)
    db.log_event("timeshift", {"portal": portal["name"], "found": found, "results": results})
    return found


def _catchup_url(portal, client, stream_id, start_ts, minutes):
    """Adress för efterhandsuppspelning. Den adressform som fungerar för källan hittas första gången
    (och sparas). Går ingen att använda används standardformen (sökväg, källans tidszon)."""
    candidates = xtream.timeshift_candidates(client, stream_id, start_ts, minutes, db.get_setting(f"portal_tz:{portal['id']}"))
    by_key = dict(candidates)
    saved = db.get_setting(TS_VARIANT.format(portal["id"]))
    if saved in by_key:
        return by_key[saved]
    if time.time() - _ts_failed_at.get(portal["id"], 0) > TS_DISCOVERY_COOLDOWN_S:
        found = _discover_timeshift(portal, candidates)
        if found:
            return by_key[found]
        _ts_failed_at[portal["id"]] = time.time()
    return candidates[0][1]


def _forget_timeshift_variant(portal_id):
    """Den sparade formen gav inget (tomt svar): sök på nytt nästa gång."""
    if portal_id is None:
        return
    db.set_setting(TS_VARIANT.format(portal_id), None)
    _ts_failed_at.pop(portal_id, None)

# Senaste strömadressen per session, bara i minnet (den innehåller kontouppgifter och får aldrig loggas).
# Används för att undersöka vad källan svarar när boxen inte kan läsa strömmen.
_recent_urls = {}
_RECENT_URLS_MAX = 100
_probe_last = {}   # device_id -> tid för senaste undersökning
PROBE_MIN_GAP_S = float(os.environ.get("TVHUB_PROBE_MIN_GAP", "60"))   # kortare bara i tester
PROBE_BYTES = 512
PROBE_TIMEOUT_S = 8


def _remember_url(session_id, url, adult, portal_id=None, kind=None):
    with _pin_lock_urls:
        _recent_urls[session_id] = (url, bool(adult), portal_id, kind)
        while len(_recent_urls) > _RECENT_URLS_MAX:
            _recent_urls.pop(next(iter(_recent_urls)))


_pin_lock_urls = threading.Lock()


def _printable(data, limit=160):
    """Svarets första byte som text om det ser ut som text, annars som hex. Aldrig mer än limit tecken."""
    head = bytes(data[:limit])
    if head and all(b in (9, 10, 13) or 32 <= b < 127 for b in head):
        return " ".join(head.decode("ascii").split())[:limit]
    return "binärt: " + head[:8].hex(" ")


def probe_stream(session_id):
    """Hämtar de första byten av strömmen och loggar vad källan svarade (statuskod, innehållstyp, början).
    Körs bara när boxen inte kan läsa strömmen. Strömadressen loggas aldrig."""
    import urllib.error
    import urllib.request
    with _pin_lock_urls:
        entry = _recent_urls.get(session_id)
    if not entry:
        return None
    url, adult = entry[0], entry[1]
    detail = {"adult": adult}
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": CFG["user_agent"], "Range": f"bytes=0-{PROBE_BYTES - 1}"})
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as resp:
            body = resp.read(PROBE_BYTES)
            detail.update(status=resp.status, content_type=(resp.headers.get("Content-Type") or "")[:80],
                          length=resp.headers.get("Content-Length"), bytes=len(body))
            detail["start"] = _printable(body)
            detail["video"] = _looks_like_video(resp.status, resp.headers.get("Content-Type"), body)
    except urllib.error.HTTPError as exc:
        detail.update(status=exc.code, content_type=(exc.headers.get("Content-Type") or "")[:80], video=False)
        try:
            detail["start"] = _printable(exc.read(PROBE_BYTES))
        except Exception:
            pass
    except Exception as exc:
        detail["error"] = type(exc).__name__
    if detail.get("length") is not None:
        detail["length"] = str(detail["length"])[:20]
    if adult:
        detail.pop("start", None)   # vuxeninnehåll: ingen text ur svaret i loggen
    return detail


def request_play(*args, **kwargs):
    """Kontroll av andra boxar och skapandet av sessionen sker under ett lås, så att två boxar som
    startar samtidigt inte båda får ström."""
    with _play_lock:
        return _request_play(*args, **kwargs)


def _recording_playable(rec):
    """Ett inspelningsjobb får bara hämta en ström när det är aktuellt: inte redan klart, och en
    direktinspelning bara nära sin sändningstid."""
    if rec["status"] not in ("scheduled", "recording", "failed"):
        return False
    if rec["source"] == "live" and rec["status"] == "scheduled":
        now = time.time()
        return rec["start"] - 1800 <= now <= rec["end"] + 3600
    return True


def _request_play(device, kind, item_id, ext=None, force=False, exclude_portals=(), name=None,
                  scope=None, series_id=None, start=None, duration=None, icon=None, recording_id=None,
                  viewer_id=None):
    """recording_id: strömmen är en inspelning. Den räknas inte av anslutningsvakten
    (man kan titta på en kanal och spela in en annan) och avslutar inte boxens tittande.

    Returnerar ett av:
    {"status": "conflict", ...}  en annan box tittar, fråga användaren
    {"status": "waiting", ...}   övertagande pågår, fråga igen om en sekund
    {"status": "ok", "url": ..., "session_id": ...}
    {"status": "error", "error": ...}
    """
    if kind not in ("live", "vod", "episode", "catchup"):
        return {"status": "error", "error": "Okänd typ"}
    item_id = str(item_id)
    if not SAFE_ID.match(item_id):
        return {"status": "error", "error": "Ogiltigt id"}
    series_id = str(series_id) if series_id not in (None, "") else None
    if series_id is not None and not SAFE_ID.match(series_id):
        return {"status": "error", "error": "Ogiltigt serie-id"}

    recording = None
    if recording_id is not None:
        recording = recordings.lookup_for_play(device, recording_id)
        if recording is None or str(recording["stream_id"]) != item_id or RECORD_KIND.get(recording["source"]) != kind \
                or not _recording_playable(recording):
            return {"status": "error", "error": "Inspelningen finns inte längre."}
        if kind in ("vod", "episode"):
            ext = ext or recording.get("ext")
            series_id = series_id or recording.get("series_id")

    # Boxens läge och profil. En inspelning prövades mot läget när den planerades.
    if scope is not None and recording is None:
        if kind in ("live", "vod", "catchup"):
            ref = ("live" if kind == "catchup" else kind, item_id)
        else:
            ref = ("series", series_id) if series_id else None
        if ref is None:
            if scope.group_id is not None or scope.adult:
                return {"status": "error", "error": "Finns inte."}
        elif not catalog.in_scope(ref[0], ref[1], scope) and \
                (catalog.get_item(*ref) is not None or scope.group_id is not None):
            return {"status": "error", "error": "Finns inte."}

    # Bara det som finns i källans lista får spelas (kanaler, filmer och serier), inte påhittade id:n.
    if recording is None:
        if kind in ("live", "catchup") and catalog.get_item("live", item_id) is None:
            return {"status": "error", "error": "Kanalen finns inte i listan."}
        if kind == "vod" and catalog.get_item("vod", item_id) is None:
            return {"status": "error", "error": "Filmen finns inte i listan."}
        # Avsnitt finns inte i listan på egen hand; ett angivet serie-id ska åtminstone vara en känd serie.
        if kind == "episode" and series_id and catalog.get_item("series", series_id) is None:
            return {"status": "error", "error": "Serien finns inte i listan."}

    if kind == "catchup":
        try:
            start, duration = int(start), int(duration)
        except (TypeError, ValueError):
            return {"status": "error", "error": "Starttid och längd krävs"}
        if not 0 < duration <= 600 or start > time.time():
            return {"status": "error", "error": "Ogiltig tidpunkt"}

    if (db.get_setting("server_test_until", 0) or 0) > time.time():
        return {"status": "waiting", "retry_ms": 2000,
                "message": "Servern kör ett kapacitetstest. Uppspelningen startar strax."}

    others = [] if recording else _active_rows(exclude_device=device["id"], watching_only=True)
    if others:
        now_s = time.time()
        pending = [o for o in others if o["stop_requested"]]
        if not force and not pending:
            o = others[0]
            shown = ADULT_LABEL if o["adult"] else o["item_name"]
            return {
                "status": "conflict",
                "conflict": {"device": o["device_name"], "item_name": shown, "since": o["started"]},
                "message": f"{o['device_name']} tittar redan. Toucan tillåter bara en ström åt gången.",
            }
        for o in others:
            if not o["stop_requested"]:
                db.x("UPDATE sessions SET stop_requested = ? WHERE id = ?", (int(now_s), o["id"]))
                db.log_event("takeover_requested", {"by": device["name"], "from": o["device_name"]},
                             device_id=device["id"], session_id=o["id"])
        still_waiting = False
        for o in _active_rows(exclude_device=device["id"], watching_only=True):
            requested = o["stop_requested"] or int(now_s)
            if now_s - requested >= TAKEOVER_TIMEOUT_S:
                _end(o["id"], "takeover_timeout")
            else:
                still_waiting = True
        if still_waiting:
            return {"status": "waiting", "retry_ms": 1000,
                    "message": "Väntar på att den andra boxen ska stoppa…"}

    portal = portals.best_portal(exclude=tuple(int(p) for p in exclude_portals if str(p).isdigit()))
    if portal is None:
        db.log_event("play_no_portal", {"type": kind, "id": item_id}, device_id=device["id"])
        return {"status": "error", "error": "Ingen portal fungerar just nu. Se Diagnostik i webbadminen."}

    client = portals.client(portal)
    item = None
    if kind in ("live", "catchup"):
        item = catalog.get_item("live", item_id)
        ext_final = portals.output_format(portal)
        display = item["name"] if item else (name or f"Kanal {item_id}")
        if kind == "catchup":
            display = (name or display)[:120]
            url = _catchup_url(portal, client, item_id, start, duration)
        else:
            url = client.stream_url("live", item_id, ext_final)
    elif kind == "vod":
        item = catalog.get_item("vod", item_id)
        ext_final = (item and item["ext"]) or (ext if ext and SAFE_EXT.match(ext) else "mp4")
        display = item["name"] if item else (name or f"Film {item_id}")
        url = client.stream_url("vod", item_id, ext_final)
    else:
        ext_final = ext if ext and SAFE_EXT.match(ext) else "mp4"
        display = (name or f"Avsnitt {item_id}")[:120]
        url = client.stream_url("episode", item_id, ext_final)
    if icon is None and item:
        icon = item.get("icon")
    if icon is None and series_id:
        parent = catalog.get_item("series", series_id)
        icon = parent["icon"] if parent else None

    # Avsluta enhetens egna tidigare sessioner (kanalbyte), först när den nya är beviljad.
    # Inspelningar påverkar inte tittandet och tvärtom.
    if recording is None:
        for row in db.q("SELECT id FROM sessions WHERE device_id = ? AND ended IS NULL "
                        "AND type NOT IN ('record', 'record_archive', 'record_vod')", (device["id"],)):
            _end(row["id"], "replaced")
        adult = 1 if scope is not None and scope.adult else 0
    else:
        adult = recording["adult"]
        display = f"Inspelning: {recording['title']}"[:120]
    session_id = secrets.token_hex(12)
    session_type = kind if recording is None else \
        {"live": "record", "catchup": "record_archive"}.get(kind, "record_vod")
    db.x(
        """INSERT INTO sessions (id, device_id, type, item_id, item_name, portal_id, started, last_heartbeat,
           adult, series_id, ext, icon, viewer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, device["id"], session_type, item_id, display, portal["id"], db.iso(), db.iso(),
         adult, series_id, ext_final, str(icon)[:500] if icon else None, viewer_id),
    )
    _remember_url(session_id, url, adult, portal["id"], kind)
    if kind == "catchup" and recording is None and start:
        # Mina program: var man är i ett program ur arkivet sparas per profil.
        try:
            db.x("UPDATE sessions SET catchup_start = ? WHERE id = ?", (int(start), session_id))
        except (TypeError, ValueError):
            pass
    if recording is not None:
        recordings.attach_session(device, recording["id"], item_id, session_id)
    db.log_event("record" if recording else "play",
                 {"type": kind, "id": "" if adult else item_id, "name": "18+" if adult else display, "portal": portal["name"]},
                 device_id=device["id"], session_id=session_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "url": url,
        "portal_id": portal["id"],
        "portal_name": portal["name"],
        "name": display,
        "heartbeat_ms": 10000,
        "user_agent": CFG["user_agent"],
        "resume": extras.resume_position(adult, kind, item_id, scope.pid if scope is not None else None)
        if kind in ("vod", "episode") and not (scope is not None and scope.private) else None,
        "archive_days": int((item or {}).get("archive_days") or 0) if kind in ("live", "catchup") else 0,
    }


_METRIC_NUMBERS = ("bandwidth_bps", "stalls", "stall_ms", "position_ms", "duration_ms", "buffered_ms",
                   "bitrate_bps", "recorded_bytes")
_RESOLUTION = re.compile(r"^\d{1,5}x\d{1,5}$")


def clean_metrics(metrics):
    """Boxens mätvärden: bara kända fält med rätt typ. Allt annat kastas (webbadminen visar värdena)."""
    if not isinstance(metrics, dict):
        return {}
    out = {}
    for key in _METRIC_NUMBERS:
        value = metrics.get(key)
        if type(value) in (int, float) and value == value and 0 <= value <= 10**13:
            out[key] = int(value)
    portal = metrics.get("portal")
    if isinstance(portal, str):
        out["portal"] = re.sub(r"[^\w .\-]", "", portal)[:60]
    resolution = metrics.get("resolution")
    if isinstance(resolution, str) and _RESOLUTION.match(resolution):
        out["resolution"] = resolution
    if metrics.get("cast") is True:
        out["cast"] = True
    return out


def heartbeat(device, session_id, metrics):
    row = db.q1("SELECT * FROM sessions WHERE id = ? AND device_id = ?", (session_id, device["id"]))
    if not row:
        return {"stop": True, "reason": "unknown_session"}
    if row["ended"]:
        return {"stop": row["end_reason"] != "replaced", "reason": row["end_reason"]}
    metrics = clean_metrics(metrics)
    db.x("UPDATE sessions SET last_heartbeat = ?, metrics = ? WHERE id = ?",
         (db.iso(), json.dumps(metrics), session_id))
    extras.update_progress(row, metrics.get("position_ms"), metrics.get("duration_ms"))
    if row["stop_requested"] and row["type"] in RECORD_TYPES:
        return {"stop": True, "reason": "record_stopped", "message": "Inspelningen stoppades från webbadminen."}
    if row["stop_requested"]:
        return {"stop": True, "reason": "takeover",
                "message": "En annan box tog över uppspelningen."}
    return {"stop": False}


def end_session(device, session_id, reason="stopped", metrics=None):
    row = db.q1("SELECT * FROM sessions WHERE id = ? AND device_id = ?", (session_id, device["id"]))
    if row:
        metrics = clean_metrics(metrics)
        if metrics:
            extras.update_progress(row, metrics.get("position_ms"), metrics.get("duration_ms"))
        _end(session_id, str(reason)[:40])
        db.log_event("stop", {"reason": reason}, device_id=device["id"], session_id=session_id)
    return bool(row)


EVENT_KINDS = {"stall", "error", "failover", "speedtest", "start", "quality", "cast"}


def report(device, kind, session_id, detail):
    if kind not in EVENT_KINDS:
        raise ValueError("Okänd händelsetyp")
    if not isinstance(detail, dict):
        detail = {}
    detail = {k: v for k, v in list(detail.items())[:30]}
    db.log_event(kind, detail, device_id=device["id"], session_id=session_id)
    if kind == "error" and str(detail.get("code", "")).startswith("ERROR_CODE_PARSING_CONTAINER") and session_id:
        _start_probe(device, session_id)


def _start_probe(device, session_id):
    now = time.time()
    with _pin_lock_urls:
        if now - _probe_last.get(device["id"], 0) < PROBE_MIN_GAP_S:
            return
        _probe_last[device["id"]] = now

    def run():
        try:
            detail = probe_stream(session_id)
            if detail is not None:
                db.log_event("probe", detail, device_id=device["id"], session_id=session_id)
                with _pin_lock_urls:
                    entry = _recent_urls.get(session_id)
                if entry and entry[3] == "catchup" and not detail.get("error") and detail.get("video") is False:
                    _forget_timeshift_variant(entry[2])
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name="probe").start()


def cleanup():
    """Stänger sessioner utan heartbeat och rensar gammal historik."""
    db.x("UPDATE sessions SET ended = last_heartbeat, end_reason = 'timeout' "
         "WHERE ended IS NULL AND last_heartbeat < ?", (db.ago(minutes=5),))


# ---------- PIN-kod för vuxenläget ----------
# Valfri. Utan kod fungerar vuxenläget som förut. Med kod måste appen låsa upp hos TV Hub och
# skicka med nyckeln (X-TVHub-Unlock); annars får boxen vanligt läge även om den ber om vuxenläge.
UNLOCK_S = 3 * 3600
MAX_TRIES = 5
LOCKOUT_S = 300
_unlocks = {}   # device_id -> (nyckel, giltig till)
_adult_limit = limits.AttemptLimiter(MAX_TRIES, LOCKOUT_S)
_pin_lock = threading.Lock()


def _pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()


def adult_pin_set():
    return bool(db.get_setting("adult_pin"))


def set_adult_pin(pin):
    pin = str(pin or "").strip()
    if not pin:
        db.set_setting("adult_pin", None)
        with _pin_lock:
            _unlocks.clear()
        return False
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("Koden ska vara 4–8 siffror.")
    salt = secrets.token_hex(16)
    db.set_setting("adult_pin", {"salt": salt, "hash": _pin_hash(pin, salt)})
    with _pin_lock:
        _unlocks.clear()
    return True


def unlock_adult(device, pin):
    """Returnerar {"pin_required": bool, "token": str|None, "error": str|None}."""
    stored = db.get_setting("adult_pin")
    if not device["allow_adult"]:
        return {"pin_required": bool(stored), "token": None, "error": "Den här boxen har inte vuxenläge."}
    if not stored or not _box_requires_pin(device):
        return {"pin_required": False, "token": "", "error": None}
    if pin in (None, ""):
        return {"pin_required": True, "token": None, "error": None}
    now = time.time()
    # Försöket räknas innan hashningen, så att parallella gissningar inte slinker förbi spärren.
    wait = _adult_limit.reserve(device["id"])
    if wait is not None:
        return {"pin_required": True, "token": None, "error": f"För många försök. Vänta {wait // 60 + 1} min."}
    if not hmac.compare_digest(_pin_hash(str(pin), stored["salt"]), stored["hash"]):
        time.sleep(1)
        return {"pin_required": True, "token": None, "error": "Fel kod."}
    token = secrets.token_urlsafe(24)
    _adult_limit.success(device["id"])
    with _pin_lock:
        _unlocks[device["id"]] = (token, now + UNLOCK_S)
    return {"pin_required": True, "token": token, "error": None}


def _box_requires_pin(device):
    keys = device.keys() if hasattr(device, "keys") else []
    return bool(device["pin_required"]) if "pin_required" in keys else True


def adult_unlocked(device, token):
    """Får boxen vuxenläge just nu? Utan PIN (eller om boxen inte kräver den): ja. Annars bara med giltig nyckel."""
    if not adult_pin_set() or not _box_requires_pin(device):
        return True
    with _pin_lock:
        entry = _unlocks.get(device["id"])
    return bool(entry and token and hmac.compare_digest(entry[0], token) and time.time() < entry[1])


def lock_adult(device_id):
    with _pin_lock:
        _unlocks.pop(device_id, None)
