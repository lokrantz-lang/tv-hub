"""Fjärrstart: säg åt en box att spela något. Används av röstkommandon (Home Assistant/Google),
"Spela på TV:n" i telefonen och "Spela på box" i webbadminen.

Boxarna håller en öppen förfrågan (long-poll) mot /device/commands och får kommandot direkt.

Röstkommandon: TV Hub skapar ett skript i Home Assistant per kommando. Skriptet kan slå på TV:n,
byta källa och skickar sedan händelsen tv_hub_command. Tillägget lyssnar på den händelsen via
Home Assistants websocket och skickar kommandot till boxen. Skripten exponeras mot Google som vanligt.
"""
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict, deque

from . import catalog, db, extras, remote
from .catalog import Scope

NORMAL = Scope(adult=0)
COMMAND_TTL = 120
ONLINE_S = 70
EVENT_TYPE = "tv_hub_command"

ACTIONS = {
    "continue": "Fortsätt titta (det senaste)",
    "series": "Serie: fortsätt där ni var",
    "program": "Senaste programmet ur arkivet",
    "channel": "Kanal direkt",
    "vod": "Film",
    "night": "Filmkväll",
    "pause": "Pausa",
    "resume": "Fortsätt spela",
    "stop": "Stäng spelaren",
}

_cond = threading.Condition()
_queues = defaultdict(deque)
_last_poll = {}
_listener = {"connected": False, "error": None, "since": None, "events": 0}


class ControlError(Exception):
    pass


# ====================================================================
# Kö till boxarna
# ====================================================================
def enqueue(device_id, command):
    command = dict(command, created=time.time())
    with _cond:
        q = _queues[int(device_id)]
        q.append(command)
        while len(q) > 10:
            q.popleft()
        _cond.notify_all()
    db.log_event("remote_command", {"action": command.get("action"), "name": command.get("name")},
                 device_id=int(device_id))
    return command


def wait(device_id, timeout):
    """Long-poll från boxen. Returnerar kommandon som väntar (eller tom lista efter timeout)."""
    device_id = int(device_id)
    end = time.time() + timeout
    with _cond:
        _last_poll[device_id] = time.time()
        while True:
            q = _queues[device_id]
            now = time.time()
            while q and now - q[0]["created"] > COMMAND_TTL:
                q.popleft()
            if q:
                out = list(q)
                q.clear()
                _last_poll[device_id] = time.time()
                return out
            left = end - now
            if left <= 0:
                _last_poll[device_id] = time.time()
                return []
            _cond.wait(min(left, 5))
            _last_poll[device_id] = time.time()


def online(device_id):
    return time.time() - _last_poll.get(int(device_id), 0) < ONLINE_S


def boxes(exclude=None):
    out = []
    for row in db.q("SELECT id, name, model FROM devices ORDER BY id"):
        if row["model"] == "integration" or row["id"] == exclude:
            continue
        out.append({"id": row["id"], "name": row["name"], "online": online(row["id"])})
    return out


# ====================================================================
# Vad ska spelas
# ====================================================================
def _first(kind, query):
    query = str(query or "").strip()
    if not query:
        raise ControlError("Ange vad som ska spelas.")
    found = catalog.search(query, kind, limit=10, scope=NORMAL)
    if not found:
        raise ControlError(f"Hittade inget som heter \"{query}\".")
    return found[0]


def _series_play(series_id, series_name=None):
    from .server import series_detail  # sen import: server importerar den här modulen
    detail = series_detail(series_id)
    name = series_name or detail.get("name") or "Serie"
    episodes = []
    for season in detail.get("seasons") or []:
        for i, ep in enumerate(season.get("episodes") or []):
            num = ep.get("num") or i + 1
            episodes.append({"id": str(ep["id"]), "title": f"{name} S{season['season']}E{num}", "ext": ep.get("ext") or "mp4"})
    if not episodes:
        raise ControlError(f"{name} har inga avsnitt.")
    progress, watched = extras.series_progress(0, series_id)
    index = next((i for i, ep in enumerate(episodes) if ep["id"] in progress), None)
    if index is None:
        last = max((i for i, ep in enumerate(episodes) if ep["id"] in watched), default=-1)
        index = min(last + 1, len(episodes) - 1)
    ep = episodes[index]
    cover = (catalog.get_item("series", series_id) or {}).get("icon") or detail.get("cover")
    return {
        "action": "play", "type": "episode", "id": ep["id"], "name": ep["title"], "ext": ep["ext"],
        "series_id": str(series_id), "icon": cover, "auto_resume": True,
        "queue_ids": [e["id"] for e in episodes], "queue_names": [e["title"] for e in episodes],
        "queue_exts": [e["ext"] for e in episodes], "queue_index": index,
    }


def resolve(action, params=None):
    """Översätter ett kommando till exakt vad boxen ska göra. ControlError med förklaring om det inte går."""
    params = params or {}
    if action in ("pause", "resume", "stop"):
        return {"action": action, "name": ACTIONS[action]}
    if action == "night":
        return {"action": "night", "viewer": params.get("viewer_id"), "name": "Filmkväll"}
    if action == "continue":
        row = db.q1("SELECT * FROM progress WHERE adult = 0 ORDER BY updated DESC LIMIT 1")
        if not row:
            raise ControlError("Det finns inget att fortsätta titta på.")
        if row["type"] == "episode" and row["series_id"]:
            return _series_play(row["series_id"])
        return {"action": "play", "type": "vod", "id": row["item_id"], "name": row["name"], "ext": row["ext"],
                "icon": row["icon"], "auto_resume": True}
    if action == "series":
        sid = params.get("series_id")
        if sid and catalog.in_scope("series", sid, NORMAL):
            return _series_play(str(sid))
        series = _first("series", params.get("query"))
        return _series_play(series["id"], series["name"])
    if action == "vod":
        vid = params.get("vod_id")
        item = catalog.get_item("vod", vid) if vid and catalog.in_scope("vod", vid, NORMAL) else _first("vod", params.get("query"))
        return {"action": "play", "type": "vod", "id": item["id"], "name": item["name"], "ext": item.get("ext"),
                "icon": item.get("icon"), "auto_resume": True}
    if action == "channel":
        cid = params.get("channel_id")
        item = catalog.get_item("live", cid) if cid and catalog.in_scope("live", cid, NORMAL) else _first("live", params.get("query"))
        return {"action": "play", "type": "live", "id": item["id"], "name": item["name"], "icon": item.get("icon")}
    if action == "program":
        cid = params.get("channel_id")
        channel = catalog.get_item("live", cid) if cid else None
        if not channel or not catalog.in_scope("live", cid, NORMAL):
            raise ControlError("Välj kanal för programmet.")
        title = str(params.get("query") or "").strip().casefold()
        if not title:
            raise ControlError("Ange programmets namn.")
        from .server import guide_for
        guide = guide_for(cid)
        now = time.time()
        matches = [p for p in guide["programs"] if title in str(p.get("title") or "").casefold()]
        past = [p for p in matches if p["playable"] and p["end"] <= now]
        if past:
            p = max(past, key=lambda x: x["start"])
            minutes = max(1, int((p["end"] - p["start"]) // 60))
            clock = time.strftime("%H:%M", time.localtime(p["start"]))
            return {"action": "play", "type": "catchup", "id": str(cid), "name": f"{channel['name']}: {p['title']} {clock}",
                    "catchup_start": int(p["start"]), "catchup_minutes": minutes, "icon": channel.get("icon")}
        if any(p["now"] for p in matches):
            return {"action": "play", "type": "live", "id": str(cid), "name": channel["name"], "icon": channel.get("icon")}
        if not guide["archive_days"]:
            raise ControlError(f"{channel['name']} har inget arkiv, och \"{params.get('query')}\" sänds inte nu.")
        raise ControlError(f"Hittade inget \"{params.get('query')}\" i {channel['name']}s arkiv.")
    raise ControlError("Okänt kommando.")


def describe(command):
    if command["action"] == "play":
        what = {"live": "Kanal", "catchup": "Från arkivet", "vod": "Film", "episode": "Avsnitt"}.get(command["type"], "")
        return f"{what}: {command['name']}"
    return command.get("name") or command["action"]


def play_on(device_id, action, params=None):
    if not db.q1("SELECT 1 FROM devices WHERE id = ? AND model != 'integration'", (int(device_id),)):
        raise LookupError("Boxen finns inte.")
    command = resolve(action, params)
    enqueue(device_id, command)
    return {"ok": True, "description": describe(command), "online": online(device_id), "command": command}


def send_item(device_id, data):
    """Från telefonen: spela en bestämd titel på en box (vanligt läge)."""
    kind = data.get("type")
    item_id = str(data.get("id") or "")
    if kind == "episode":
        sid = str(data.get("series_id") or "")
        if not sid or not catalog.in_scope("series", sid, NORMAL):
            raise LookupError("Serien finns inte.")
        command = _series_play(sid)
        ids = command["queue_ids"]
        if item_id in ids:
            i = ids.index(item_id)
            command.update(id=item_id, name=command["queue_names"][i], ext=command["queue_exts"][i], queue_index=i)
    elif kind == "series":
        command = resolve("series", {"series_id": item_id})
    elif kind in ("vod", "live"):
        command = resolve("vod" if kind == "vod" else "channel", {f"{'vod' if kind == 'vod' else 'channel'}_id": item_id})
        if not command.get("id"):
            raise LookupError("Finns inte.")
    else:
        raise ValueError("Kan inte skickas till en box.")
    if not db.q1("SELECT 1 FROM devices WHERE id = ? AND model != 'integration'", (int(device_id),)):
        raise LookupError("Boxen finns inte.")
    enqueue(device_id, command)
    return {"ok": True, "description": describe(command), "online": online(device_id)}


# ====================================================================
# Röstkommandon och skript i Home Assistant
# ====================================================================
PARAM_KEYS = ("query", "channel_id", "series_id", "vod_id", "viewer_id", "tv_entity", "tv_source", "tv_delay")


def _cmd(row):
    c = dict(row)
    c["params"] = json.loads(c.get("params") or "{}")
    c["action_label"] = ACTIONS.get(c["action"], c["action"])
    c["entity_id"] = f"script.{c['script_id']}" if c.get("script_id") else None
    return c


def commands():
    rows = db.q("""SELECT v.*, d.name AS device_name FROM voice_commands v
                   LEFT JOIN devices d ON d.id = v.device_id ORDER BY v.device_id, v.id""")
    return [_cmd(r) for r in rows]


def command(cmd_id):
    row = db.q1("SELECT * FROM voice_commands WHERE id = ?", (int(cmd_id),))
    return _cmd(row) if row else None


def _clean(data):
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Ge kommandot ett namn, det är det du säger till Google.")
    action = data.get("action")
    if action not in ACTIONS:
        raise ValueError("Välj vad som ska hända.")
    try:
        device_id = int(data.get("device_id"))
    except (TypeError, ValueError):
        raise ValueError("Välj box.")
    if not db.q1("SELECT 1 FROM devices WHERE id = ?", (device_id,)):
        raise LookupError("Boxen finns inte.")
    raw = data.get("params") or {}
    params = {k: raw[k] for k in PARAM_KEYS if raw.get(k) not in (None, "")}
    if params.get("tv_entity") and not str(params["tv_entity"]).startswith("media_player."):
        raise ValueError("TV:n ska vara en media_player-entitet.")
    if "tv_delay" in params:
        params["tv_delay"] = max(0, min(int(params["tv_delay"]), 60))
    if action in ("series", "vod") and not (params.get("query") or params.get(f"{action}_id")):
        raise ValueError("Ange titeln.")
    if action == "channel" and not (params.get("query") or params.get("channel_id")):
        raise ValueError("Ange kanalen.")
    if action == "program" and not (params.get("query") and params.get("channel_id")):
        raise ValueError("Ange programnamn och kanal.")
    return name, action, device_id, params


def save_command(data, cmd_id=None):
    name, action, device_id, params = _clean(data)
    now = db.iso()
    if cmd_id is None:
        cmd_id = db.x("INSERT INTO voice_commands (name, device_id, action, params, created, updated) VALUES (?,?,?,?,?,?)",
                      (name, device_id, action, json.dumps(params), now, now)).lastrowid
    else:
        if not command(cmd_id):
            raise LookupError("Kommandot finns inte.")
        db.x("UPDATE voice_commands SET name = ?, device_id = ?, action = ?, params = ?, updated = ? WHERE id = ?",
             (name, device_id, action, json.dumps(params), now, cmd_id))
    sync_script(cmd_id)
    return command(cmd_id)


def delete_command(cmd_id):
    cmd = command(cmd_id)
    if not cmd:
        raise LookupError("Kommandot finns inte.")
    if cmd.get("script_id"):
        try:
            _core("DELETE", f"config/script/config/{cmd['script_id']}")
        except ControlError as exc:
            print(f"[fel] ta bort skript: {exc}", flush=True)
    db.x("DELETE FROM voice_commands WHERE id = ?", (int(cmd_id),))


def run_command(cmd_id):
    cmd = command(cmd_id)
    if not cmd:
        raise LookupError("Kommandot finns inte.")
    return play_on(cmd["device_id"], cmd["action"], cmd["params"])


def spoken_room(device_name):
    """"Vardagsrum (Z10 Pro)" -> "vardagsrummet"."""
    base = re.sub(r"\s*\(.*\)\s*", "", str(device_name or "")).strip()
    low = base.lower()
    if low.endswith("rum"):
        return low + "met"
    return low or "boxen"


def add_defaults(device_id, room=None):
    dev = db.q1("SELECT * FROM devices WHERE id = ?", (int(device_id),))
    if not dev:
        raise LookupError("Boxen finns inte.")
    room = (room or spoken_room(dev["name"])).strip()
    created = []
    for action, name in (("continue", f"Fortsätt titta i {room}"), ("pause", f"Pausa {room}"),
                         ("resume", f"Spela vidare i {room}"), ("stop", f"Stäng TV Hub i {room}")):
        if db.q1("SELECT 1 FROM voice_commands WHERE device_id = ? AND action = ?", (dev["id"], action)):
            continue
        created.append(save_command({"name": name, "action": action, "device_id": dev["id"],
                                     "params": _tv_defaults(dev["id"])}))
    return created


def _tv_defaults(device_id):
    """TV-inställningar från ett tidigare kommando för samma box (så att man bara anger dem en gång)."""
    for row in db.q("SELECT params FROM voice_commands WHERE device_id = ? ORDER BY id DESC", (device_id,)):
        p = json.loads(row["params"] or "{}")
        if p.get("tv_entity"):
            return {k: p[k] for k in ("tv_entity", "tv_source", "tv_delay") if p.get(k) not in (None, "")}
    return {}


def _slug(text):
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "kommando"


def script_config(cmd):
    p = cmd["params"]
    sequence = []
    tv = p.get("tv_entity")
    if tv and cmd["action"] not in ("pause", "resume", "stop"):
        sequence.append({
            "if": [{"condition": "state", "entity_id": tv, "state": "off"}],
            "then": [
                {"action": "media_player.turn_on", "target": {"entity_id": tv}, "continue_on_error": True},
                {"delay": {"seconds": int(p.get("tv_delay") or 10)}},
            ],
        })
        if p.get("tv_source"):
            sequence.append({"action": "media_player.select_source", "target": {"entity_id": tv},
                             "data": {"source": p["tv_source"]}, "continue_on_error": True})
    sequence.append({"event": EVENT_TYPE, "event_data": {"command_id": cmd["id"]}})
    return {
        "alias": cmd["name"],
        "description": "Skapat av TV Hub (röstkommando). Ändra eller ta bort i TV Hub, inte här.",
        "icon": "mdi:television-play",
        "mode": "single",
        "sequence": sequence,
    }


def sync_script(cmd_id):
    cmd = command(cmd_id)
    script_id = cmd.get("script_id") or f"tv_hub_{cmd['id']}_{_slug(cmd['name'])}"
    try:
        _core("POST", f"config/script/config/{script_id}", script_config(cmd))
        status = "ok"
    except ControlError as exc:
        status = str(exc)
    db.x("UPDATE voice_commands SET script_id = ?, script_status = ? WHERE id = ?", (script_id, status, cmd["id"]))
    return status


def _core(method, path, body=None):
    token = remote.supervisor_token()
    if not token:
        raise ControlError("Ingen kontakt med Home Assistant (körs tillägget i Home Assistant?).")
    base = os.environ.get("SUPERVISOR_API", "http://supervisor")
    request = urllib.request.Request(f"{base}/core/api/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            raw = resp.read().decode() or "null"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise ControlError(f"Home Assistant svarade HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ControlError(f"Ingen kontakt med Home Assistant: {exc}")


def media_players():
    """TV-apparater och andra mediaspelare i Home Assistant (för att slå på TV:n först)."""
    try:
        states = _core("GET", "states") or []
    except ControlError as exc:
        return {"available": False, "error": str(exc), "entities": []}
    out = []
    for s in states:
        eid = s.get("entity_id", "")
        if not eid.startswith("media_player."):
            continue
        attrs = s.get("attributes") or {}
        out.append({"entity_id": eid, "name": attrs.get("friendly_name") or eid, "state": s.get("state"),
                    "sources": attrs.get("source_list") or []})
    out.sort(key=lambda e: e["name"].lower())
    return {"available": True, "entities": out}


def script_yaml(cmd):
    """Reserv om skriptet inte kunde skapas automatiskt: text att klistra in i Home Assistant."""
    def emit(value, indent):
        pad = "  " * indent
        if isinstance(value, dict):
            lines = []
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    lines.append(emit(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
            return "\n".join(lines)
        if isinstance(value, list):
            lines = []
            for v in value:
                inner = emit(v, indent + 1).lstrip()
                lines.append(f"{pad}- {inner}")
            return "\n".join(lines)
        return f"{pad}{json.dumps(value, ensure_ascii=False)}"
    return f"{cmd['script_id']}:\n" + emit(script_config(cmd), 1)


# ====================================================================
# Lyssna på händelsen från skripten
# ====================================================================
def handle_event(data):
    try:
        cmd_id = int((data or {}).get("command_id"))
    except (TypeError, ValueError):
        return
    try:
        result = run_command(cmd_id)
        print(f"Röstkommando {cmd_id}: {result['description']}", flush=True)
    except (ControlError, LookupError, ValueError) as exc:
        db.log_event("remote_command_failed", {"command_id": cmd_id, "error": str(exc)})
        print(f"[fel] röstkommando {cmd_id}: {exc}", flush=True)


def listen_loop(stop_event):
    """Håller en websocket mot Home Assistant och tar emot tv_hub_command. Återansluter vid fel."""
    while not stop_event.is_set():
        token = remote.supervisor_token()
        if not token:
            _listener.update(connected=False, error="Ingen Supervisor-nyckel")
            stop_event.wait(300)
            continue
        ws = None
        try:
            ws = remote.WebSocket(remote.SUPERVISOR_WS, timeout=15)
            if json.loads(ws.recv_text()).get("type") != "auth_required":
                raise remote.WsError("Oväntat svar")
            ws.send_text(json.dumps({"type": "auth", "access_token": token}))
            if json.loads(ws.recv_text()).get("type") != "auth_ok":
                raise remote.WsError("Nyckeln godtogs inte")
            ws.send_text(json.dumps({"id": 1, "type": "subscribe_events", "event_type": EVENT_TYPE}))
            ws.sock.settimeout(45)
            _listener.update(connected=True, error=None, since=db.iso())
            next_id = 2
            while not stop_event.is_set():
                try:
                    msg = json.loads(ws.recv_text())
                except TimeoutError:
                    ws.send_text(json.dumps({"id": next_id, "type": "ping"}))
                    next_id += 1
                    continue
                if msg.get("type") == "event" and msg.get("id") == 1:
                    _listener["events"] += 1
                    event = msg.get("event") or {}
                    threading.Thread(target=handle_event, args=(event.get("data"),), daemon=True).start()
                elif msg.get("type") == "result" and msg.get("id") == 1 and not msg.get("success"):
                    raise remote.WsError("Prenumerationen nekades")
        except Exception as exc:
            _listener.update(connected=False, error=str(exc))
        finally:
            if ws is not None:
                ws.close()
        stop_event.wait(10)


def listener_status():
    return dict(_listener)
