"""Mina sporter och "När går …?".

Programguiden för svenska kanaler och sportkanaler hämtas i bakgrunden (en kanal i taget, med paus emellan,
var fjärde timme) och sparas i tabellen epg. Kanaler med samma EPG-id (t.ex. SVT1 och SVT1 HD) hämtas en gång.
Det gör att TV Hub kan:
  - hitta sändningar för intressena (Golf, Ridsport, IFK Norrköping …) med sökord
  - svara på "När går …?" för vilket program som helst på de kanalerna
  - fråga på boxarna och skicka notis till telefonen när en match börjar
  - planera inspelning av lagets matcher på en vald box
  - ge Home Assistant nästa match (sensor)

Sökord: en rad per alternativ. "a + b" betyder att både a och b måste finnas. Hela ord jämförs, utan hänsyn till
stora/små bokstäver och skiljetecken. "Inte"-orden stoppar en träff. Bara titeln, eller titel och beskrivning.
"""
import json
import os
import re
import threading
import time

from . import catalog, control, db, portals
from .catalog import Scope
from .xtream import XtreamError

NORMAL = Scope(adult=0)
AUTO_CATEGORY = re.compile(r"(?<![a-z])(sports?|se|sverige|sweden|svensk|svenska|nordic|norden|skandinavi\w*)(?![a-z])", re.I)
SWEDISH_NAME = re.compile(r"^\s*se\s*[:|\-]", re.I)
KEEP_BACK_S = 7 * 86400
KEEP_AHEAD_S = 8 * 86400
MAX_CHANNELS = 600

_status = {"running": False, "done": 0, "total": 0, "started": None, "finished": None, "error": None}
_wake = threading.Event()


def scan_interval():
    return int(os.environ.get("SPORT_SCAN_S", str(4 * 3600)))


def scan_pause():
    return float(os.environ.get("SPORT_SCAN_PAUSE", "1.5"))


def norm(text):
    text = catalog.normalize(text)
    return re.sub(r"[^\w ]+", " ", text).strip()


# ====================================================================
# Inställningar och intressen
# ====================================================================
DEFAULT_SETTINGS = {"categories": [], "notify_devices": [], "notify_phone": True, "lead_minutes": 5,
                    "quiet_from": "23:00", "quiet_to": "08:00"}


def settings():
    out = dict(DEFAULT_SETTINGS)
    out.update(db.get_setting("sport_settings") or {})
    return out


def _clock(value, default):
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", str(value or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        if value in (None, ""):
            return default
        raise ValueError(f"Ogiltig tid: {value} (skriv t.ex. 23:00).")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def save_settings(data):
    cur = settings()
    if "categories" in data:
        cur["categories"] = [str(c) for c in (data.get("categories") or [])][:200]
    if "notify_devices" in data:
        ids = []
        for d in data.get("notify_devices") or []:
            if db.q1("SELECT 1 FROM devices WHERE id = ? AND model != 'integration'", (int(d),)):
                ids.append(int(d))
        cur["notify_devices"] = ids
    if "notify_phone" in data:
        cur["notify_phone"] = bool(data["notify_phone"])
    if "lead_minutes" in data:
        try:
            cur["lead_minutes"] = max(0, min(60, int(data["lead_minutes"])))
        except (TypeError, ValueError):
            raise ValueError("Minuter innan ska vara ett tal mellan 0 och 60.")
    for key, default in (("quiet_from", "23:00"), ("quiet_to", "08:00")):
        if key in data:
            cur[key] = _clock(data[key], "") if data[key] else ""
    db.set_setting("sport_settings", cur)
    return cur


def _lines(value):
    if isinstance(value, str):
        value = value.splitlines()
    out = []
    for line in value or []:
        line = str(line).strip()
        if line and line not in out:
            out.append(line[:80])
    return out[:40]


def _interest(r):
    item = dict(r)
    item["keywords"] = json.loads(item["keywords"] or "[]")
    item["exclude"] = json.loads(item["exclude"] or "[]")
    for k in ("title_only", "notify", "enabled"):
        item[k] = bool(item[k])
    return item


def interests(enabled_only=False):
    rows = db.q("SELECT * FROM sport_interests " + ("WHERE enabled = 1 " if enabled_only else "") + "ORDER BY position, id")
    return [_interest(r) for r in rows]


def get_interest(interest_id):
    r = db.q1("SELECT * FROM sport_interests WHERE id = ?", (int(interest_id),))
    return _interest(r) if r else None


def _clean(data, current=None):
    cur = current or {}
    name = str(data.get("name", cur.get("name", "")) or "").strip()[:40]
    if not name:
        raise ValueError("Namn saknas.")
    keywords = _lines(data["keywords"]) if "keywords" in data else cur.get("keywords", [])
    if not keywords:
        raise ValueError("Ange minst ett sökord.")
    exclude = _lines(data["exclude"]) if "exclude" in data else cur.get("exclude", [])
    record = data.get("record_device_id", cur.get("record_device_id"))
    if record in ("", 0, "0"):
        record = None
    if record is not None:
        record = int(record)
        if not db.q1("SELECT 1 FROM devices WHERE id = ? AND model != 'integration'", (record,)):
            raise ValueError("Boxen finns inte.")
    kind = data.get("kind", cur.get("kind", "sport"))
    return {
        "name": name, "kind": kind if kind in ("sport", "team") else "sport",
        "keywords": json.dumps(keywords, ensure_ascii=False), "exclude": json.dumps(exclude, ensure_ascii=False),
        "title_only": 1 if data.get("title_only", cur.get("title_only")) else 0,
        "notify": 1 if data.get("notify", cur.get("notify")) else 0,
        "record_device_id": record,
        "enabled": 1 if data.get("enabled", cur.get("enabled", True)) else 0,
    }


def create_interest(data):
    v = _clean(data)
    pos = (db.q1("SELECT MAX(position) AS m FROM sport_interests")["m"] or 0) + 1
    new_id = db.x("""INSERT INTO sport_interests (name, kind, keywords, exclude, title_only, notify, record_device_id,
                     enabled, position, created) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (v["name"], v["kind"], v["keywords"], v["exclude"], v["title_only"], v["notify"],
                   v["record_device_id"], v["enabled"], pos, db.iso())).lastrowid
    rematch([new_id])
    return get_interest(new_id)


def update_interest(interest_id, data):
    cur = get_interest(interest_id)
    if not cur:
        raise LookupError("Finns inte.")
    v = _clean(data, cur)
    db.x("""UPDATE sport_interests SET name = ?, kind = ?, keywords = ?, exclude = ?, title_only = ?, notify = ?,
            record_device_id = ?, enabled = ? WHERE id = ?""",
         (v["name"], v["kind"], v["keywords"], v["exclude"], v["title_only"], v["notify"], v["record_device_id"],
          v["enabled"], cur["id"]))
    if "position" in data:
        db.x("UPDATE sport_interests SET position = ? WHERE id = ?", (int(data["position"]), cur["id"]))
    rematch([cur["id"]])
    return get_interest(cur["id"])


def delete_interest(interest_id):
    db.x("DELETE FROM sport_hits WHERE interest_id = ?", (int(interest_id),))
    return db.x("DELETE FROM sport_interests WHERE id = ?", (int(interest_id),)).rowcount > 0


# ====================================================================
# Matchning
# ====================================================================
def _phrase(p):
    p = norm(p)
    return re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)") if p else None


def matcher(interest):
    """Funktion (titel, beskrivning) -> bool för ett intresse."""
    groups = []
    for line in interest["keywords"]:
        parts = [_phrase(x) for x in line.split("+")]
        parts = [x for x in parts if x]
        if parts:
            groups.append(parts)
    excludes = [x for x in (_phrase(e) for e in interest["exclude"]) if x]
    title_only = interest["title_only"]

    def test(title, description):
        text = norm(title) if title_only else norm(f"{title} {description or ''}")
        if not any(all(p.search(text) for p in group) for group in groups):
            return False
        return not any(e.search(text) for e in excludes)
    return test


def rematch(interest_ids=None):
    """Räknar om vilka program som hör till intressena (efter genomsökning eller ändrade sökord)."""
    targets = [i for i in interests() if interest_ids is None or i["id"] in interest_ids]
    if not targets:
        return 0
    since = int(time.time()) - KEEP_BACK_S
    rows = db.q("SELECT epg_key, start, title, description FROM epg WHERE end > ?", (since,))
    tests = [(i["id"], matcher(i)) for i in targets if i["enabled"]]
    hits = []
    for r in rows:
        for iid, test in tests:
            if test(r["title"], r["description"]):
                hits.append((iid, r["epg_key"], r["start"]))
    with db.tx() as conn:
        for i in targets:
            conn.execute("DELETE FROM sport_hits WHERE interest_id = ?", (i["id"],))
        conn.executemany("INSERT OR IGNORE INTO sport_hits (interest_id, epg_key, start) VALUES (?,?,?)", hits)
    return len(hits)


# ====================================================================
# Genomsökning av programguiden
# ====================================================================
def _epg_key(item):
    return (item.get("epg_id") or "").strip().casefold() or f"ch:{item['id']}"


def scan_channels():
    """Kanalerna som söks igenom: valda kategorier, annars svenska kategorier och sportkategorier automatiskt,
    plus kanaler vars namn börjar med "SE:". Vuxenkategorier aldrig. Grupperade per EPG-id."""
    chosen = set(settings()["categories"])
    cats = [c for c in catalog.categories("live") if not c["adult"]]
    if chosen:
        wanted = {c["category_id"] for c in cats if c["category_id"] in chosen}
    else:
        wanted = {c["category_id"] for c in cats if not c["hidden"] and AUTO_CATEGORY.search(c["name"] or "")}
    rows = db.q("""SELECT i.item_id AS id, i.name, i.epg_id, i.category_id, i.archive_days FROM items i
                   WHERE i.type = 'live' AND i.adult = 0""")
    groups = {}
    for r in rows:
        item = dict(r)
        if item["category_id"] not in wanted and not (not chosen and SWEDISH_NAME.search(item["name"] or "")):
            continue
        groups.setdefault(_epg_key(item), []).append(item)
    out = []
    for key, items in groups.items():
        # Kanalen med arkiv och HD hämtas (arkivflaggan i guiden gäller den kanalen).
        best = max(items, key=lambda i: (int(i["archive_days"] or 0) > 0, "hd" in (i["name"] or "").casefold(), -len(i["name"] or "")))
        out.append((key, best["id"]))
    out.sort()
    return out[:MAX_CHANNELS]


def status():
    out = dict(_status)
    row = db.q1("SELECT COUNT(*) AS n, MAX(scanned) AS last FROM epg_scan")
    out["channels"] = row["n"]
    out["last_scan"] = row["last"]
    out["programs"] = db.q1("SELECT COUNT(*) AS n FROM epg")["n"]
    out["errors"] = db.q1("SELECT COUNT(*) AS n FROM epg_scan WHERE error IS NOT NULL")["n"]
    out["interval_h"] = round(scan_interval() / 3600, 1)
    return out


def scan(stop_event=None):
    """Hämtar programguiden för kanalerna. Körs i bakgrunden; en kanal i taget."""
    if _status["running"]:
        return False
    portal = portals.best_portal()
    if portal is None:
        _status["error"] = "Ingen portal fungerar just nu."
        return False
    channels = scan_channels()
    _status.update(running=True, done=0, total=len(channels), started=int(time.time()), error=None)
    try:
        client = portals.client(portal)
        now = int(time.time())
        keep = {k for k, _ in channels}
        for i, (key, stream_id) in enumerate(channels):
            if stop_event is not None and stop_event.is_set():
                break
            try:
                programs = client.full_epg(stream_id)
                error = None
            except XtreamError as exc:
                programs, error = None, str(exc)[:200]
            if programs is not None:
                rows = [(key, p["start"], p["end"], (p["title"] or "")[:200], norm(p["title"])[:200],
                         (p["description"] or "")[:400], 1 if p["archive"] else 0)
                        for p in programs if p["end"] > now - KEEP_BACK_S and p["start"] < now + KEEP_AHEAD_S]
                with db.tx() as conn:
                    conn.execute("DELETE FROM epg WHERE epg_key = ?", (key,))
                    conn.executemany("INSERT OR REPLACE INTO epg (epg_key, start, end, title, title_norm, description, archive)"
                                     " VALUES (?,?,?,?,?,?,?)", rows)
            db.x("INSERT OR REPLACE INTO epg_scan (epg_key, stream_id, scanned, programs, error) VALUES (?,?,?,?,?)",
                 (key, stream_id, int(time.time()), len(programs or []), error))
            _status["done"] = i + 1
            if scan_pause() > 0:
                time.sleep(scan_pause())
        # Kanaler som inte längre söks, och gamla program, tas bort.
        with db.tx() as conn:
            for r in conn.execute("SELECT epg_key FROM epg_scan").fetchall():
                if r["epg_key"] not in keep:
                    conn.execute("DELETE FROM epg WHERE epg_key = ?", (r["epg_key"],))
                    conn.execute("DELETE FROM epg_scan WHERE epg_key = ?", (r["epg_key"],))
            conn.execute("DELETE FROM epg WHERE end < ?", (now - KEEP_BACK_S,))
            conn.execute("DELETE FROM sport_done WHERE at < ?", (now - 30 * 86400,))
        rematch()
        db.log_event("epg_scan", {"channels": len(channels), "programs": db.q1("SELECT COUNT(*) AS n FROM epg")["n"]})
    except Exception as exc:  # noqa: BLE001
        _status["error"] = str(exc)[:200]
        print(f"[fel] programguide: {exc}", flush=True)
    finally:
        _status.update(running=False, finished=int(time.time()))
    try:
        plan_recordings()
    except Exception as exc:  # noqa: BLE001
        print(f"[fel] sportinspelningar: {exc}", flush=True)
    return True


def request_scan():
    _wake.set()


def loop(stop_event):
    first = float(os.environ.get("SPORT_SCAN_DELAY", "90"))
    _wake.wait(first)  # en begärd genomsökning ("Sök igenom nu") startar direkt
    if stop_event.is_set():
        return
    last = 0
    last_minute = 0
    while not stop_event.is_set():
        now = time.time()
        has_catalog = db.q1("SELECT 1 FROM items WHERE type = 'live' LIMIT 1")
        if has_catalog and (_wake.is_set() or now - last >= scan_interval()):
            _wake.clear()
            last = now
            scan(stop_event)
            last_minute = 0  # notiser direkt efter genomsökningen
        if now - last_minute >= 30:
            last_minute = now
            try:
                notify_due()
            except Exception as exc:  # noqa: BLE001
                print(f"[fel] sportnotiser: {exc}", flush=True)
        _wake.wait(10)


# ====================================================================
# Sändningar
# ====================================================================
def _channels_for(keys, scope):
    """Kanalerna (i boxens urval) för EPG-nycklarna."""
    out = {}
    for key in keys:
        if key.startswith("ch:"):
            rows = db.q("SELECT item_id FROM items WHERE type = 'live' AND item_id = ?", (key[3:],))
        else:
            rows = db.q("SELECT item_id FROM items WHERE type = 'live' AND lower(epg_id) = ?", (key,))
        items = []
        for r in rows:
            if catalog.in_scope("live", r["item_id"], scope):
                item = catalog.get_item("live", r["item_id"])
                if item and not item.get("adult"):
                    items.append(item)
        out[key] = items
    return out


def _channel_rank(item):
    name = (item.get("name") or "").casefold()
    return (bool(SWEDISH_NAME.search(name)) or "sverige" in (item.get("category_name") or "").casefold(),
            "hd" in name, int(item.get("archive_days") or 0) > 0, -len(name))


def _day(ts):
    lt, now = time.localtime(ts), time.localtime()
    if (lt.tm_year, lt.tm_yday) == (now.tm_year, now.tm_yday):
        return "Idag"
    tomorrow = time.localtime(time.time() + 86400)
    if (lt.tm_year, lt.tm_yday) == (tomorrow.tm_year, tomorrow.tm_yday):
        return "Imorgon"
    yday = time.localtime(time.time() - 86400)
    if (lt.tm_year, lt.tm_yday) == (yday.tm_year, yday.tm_yday):
        return "Igår"
    days = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
    return f"{days[lt.tm_wday].capitalize()} {lt.tm_mday}/{lt.tm_mon}"


def when_label(start, end=None):
    clock = time.strftime("%H:%M", time.localtime(start))
    if end and start <= time.time() < end:
        return f"Nu · {clock}–{time.strftime('%H:%M', time.localtime(end))}"
    return f"{_day(start)} {clock}"


def build_events(rows, scope, interest_names=None, now=None):
    """Programrader -> sändningar. Samma program på flera kanaler (samma titel och starttid) blir en."""
    now = now or int(time.time())
    groups = {}
    for r in rows:
        k = (r["title_norm"], r["start"])
        g = groups.setdefault(k, {"rows": [], "interests": set()})
        g["rows"].append(r)
        if "interest_id" in r.keys():
            g["interests"].add(r["interest_id"])
    channels = _channels_for({r["epg_key"] for r in rows}, scope)
    out = []
    for (_, start), g in groups.items():
        r0 = g["rows"][0]
        cands = [c for r in g["rows"] for c in channels.get(r["epg_key"], [])]
        if not cands:
            continue
        end = r0["end"]
        live = start <= now < end
        past = end <= now
        if past:
            playable = [c for c in cands if int(c.get("archive_days") or 0) * 86400 >= now - start]
            if not playable:
                continue
            ch = max(playable, key=_channel_rank)
        else:
            ch = max(cands, key=_channel_rank)
        others = sorted({c["name"] for c in cands if c["id"] != ch["id"]})[:4]
        names = sorted(interest_names.get(i, "") for i in g["interests"]) if interest_names else []
        event = {
            "id": f"{ch['id']}:{start}", "title": r0["title"], "description": r0["description"] or "",
            "start": start, "end": end, "state": "now" if live else ("past" if past else "upcoming"),
            "channel_id": ch["id"], "channel": ch["name"], "icon": ch.get("icon"), "also": others,
            "interests": [n for n in names if n], "when": when_label(start, end),
        }
        if live:
            event["command"] = {"action": "play", "type": "live", "id": str(ch["id"]), "name": ch["name"], "icon": ch.get("icon")}
        elif past:
            event["command"] = {"action": "play", "type": "catchup", "id": str(ch["id"]),
                                "name": f"{ch['name']}: {r0['title']} {time.strftime('%H:%M', time.localtime(start))}",
                                "catchup_start": int(start), "catchup_minutes": max(1, int((end - start) // 60)),
                                "icon": ch.get("icon")}
        out.append(event)
    out.sort(key=lambda e: e["start"])
    return out


def _split(events, upcoming_days=7, limit=40):
    now = time.time()
    return {
        "now": [e for e in events if e["state"] == "now"],
        "upcoming": [e for e in events if e["state"] == "upcoming" and e["start"] < now + upcoming_days * 86400][:limit],
        "past": sorted([e for e in events if e["state"] == "past"], key=lambda e: -e["start"])[:limit],
    }


def events(scope=NORMAL, interest_id=None, now=None):
    """Sändningar för intressena: just nu, kommande och i efterhand (bara det som finns i arkivet)."""
    now = now or int(time.time())
    names = {i["id"]: i["name"] for i in interests(enabled_only=True)}
    args = [now - KEEP_BACK_S]
    cond = ""
    if interest_id:
        cond = " AND h.interest_id = ?"
        args.append(int(interest_id))
    rows = db.q(f"""SELECT h.interest_id, e.* FROM sport_hits h
                    JOIN epg e ON e.epg_key = h.epg_key AND e.start = h.start
                    JOIN sport_interests s ON s.id = h.interest_id AND s.enabled = 1
                    WHERE e.end > ?{cond} ORDER BY e.start""", args)
    return build_events(rows, scope, names, now)


def overview(scope=NORMAL):
    all_events = events(scope)
    out = _split(all_events)
    out["interests"] = [{"id": i["id"], "name": i["name"], "kind": i["kind"],
                         "count": sum(1 for e in all_events if i["name"] in e["interests"])}
                        for i in interests(enabled_only=True)]
    out["status"] = {"last_scan": status()["last_scan"], "running": _status["running"]}
    return out


def when(query, scope=NORMAL):
    """När går …? Sändningar på kanalerna i programguiden, plus filmer och serier i biblioteket."""
    words = norm(query).split()
    if not words or len(" ".join(words)) < 2:
        raise ValueError("Skriv vad du letar efter.")
    now = int(time.time())
    cond = " AND ".join("title_norm LIKE ?" for _ in words)
    args = [now - KEEP_BACK_S] + [f"%{w}%" for w in words]
    rows = db.q(f"SELECT * FROM epg WHERE end > ? AND {cond} ORDER BY start LIMIT 400", args)
    found = build_events(rows, scope, None, now)
    out = _split(found, upcoming_days=8, limit=30)
    library = []
    for kind in ("series", "vod"):
        for item in catalog.search(query, kind, limit=6, scope=scope):
            library.append(item)
    out["library"] = library[:8]
    out["channels"] = [c for c in catalog.search(query, "live", limit=6, scope=scope)]
    out["query"] = query
    out["covered"] = status()["channels"]
    return out


def next_match(scope=NORMAL):
    """Nästa (eller pågående) sändning för intressena, för Home Assistant och röststyrning."""
    evs = [e for e in events(scope) if e["state"] != "past"]
    return evs[0] if evs else None


def live_now(interest_id=None, scope=NORMAL):
    """Röststyrning: det som sänds nu för intresset (eller något av intressena)."""
    evs = events(scope, interest_id)
    now_evs = [e for e in evs if e["state"] == "now"]
    if now_evs:
        return now_evs[0]["command"], now_evs[0]
    nxt = next((e for e in evs if e["state"] == "upcoming"), None)
    who = (get_interest(interest_id) or {}).get("name") if interest_id else "dina sporter"
    if nxt:
        raise control.ControlError(f"Inget med {who} sänds just nu. Nästa: {nxt['title']}, {nxt['when'].lower()} på {nxt['channel']}.")
    raise control.ControlError(f"Inget med {who} sänds just nu, och inget är planerat i programguiden.")


# ====================================================================
# Notis när matchen börjar och inspelning
# ====================================================================
def _quiet(ts):
    s = settings()
    a, b = s.get("quiet_from"), s.get("quiet_to")
    if not a or not b:
        return False
    lt = time.localtime(ts)
    m = lt.tm_hour * 60 + lt.tm_min
    fa = int(a[:2]) * 60 + int(a[3:])
    fb = int(b[:2]) * 60 + int(b[3:])
    return (fa <= m or m < fb) if fa > fb else (fa <= m < fb)


def _done(key):
    try:
        db.x("INSERT INTO sport_done (key, at) VALUES (?, ?)", (key, int(time.time())))
        return True
    except Exception:  # noqa: BLE001 - finns redan
        return False


def notify_due(now=None):
    """Matcher (intressen med notis) som börjar inom några minuter: fråga på boxarna och skicka notis."""
    now = now or int(time.time())
    s = settings()
    lead = int(s.get("lead_minutes") or 0) * 60
    wanted = {i["id"] for i in interests(enabled_only=True) if i["notify"]}
    if not wanted:
        return 0
    sent = 0
    for e in events(NORMAL, now=now):
        if e["state"] == "past" or not e["interests"]:
            continue
        if not (e["start"] - lead - 30 <= now < e["start"] + 600):
            continue
        ids = {i["id"] for i in interests(enabled_only=True) if i["name"] in e["interests"]}
        if not ids & wanted or _quiet(e["start"]):
            continue
        if not _done(f"notify:{e['title']}:{e['start']}"):
            continue
        for device_id in s.get("notify_devices") or []:
            control.enqueue(device_id, {"action": "reminder", "name": "Mina sporter", "channel_id": e["channel_id"],
                                        "channel": e["channel"], "title": e["title"], "start": e["start"],
                                        "end": e["end"], "adult": 0})
        if s.get("notify_phone"):
            try:
                _phone(e)
            except Exception as exc:  # noqa: BLE001 - notisen får aldrig stoppa resten
                print(f"[fel] sportnotis till telefonen: {exc}", flush=True)
        db.log_event("sport_notify", {"title": e["title"], "channel": e["channel"]})
        sent += 1
    return sent


def _phone(e):
    from . import reminders
    service = reminders.settings().get("notify")
    if not service:
        return False
    clock = time.strftime("%H:%M", time.localtime(e["start"]))
    control._core("POST", f"services/notify/{service}",
                  {"title": f"{clock}: {e['title']}", "message": f"{e['channel']}. " + ", ".join(e["interests"])})
    return True


PAD_BEFORE_S = 120
PAD_AFTER_S = 15 * 60


def plan_recordings(now=None):
    """Intressen med "Spela in på": kommande sändningar planeras som inspelningar på boxen (en gång per sändning;
    tar man bort inspelningen planeras den inte igen)."""
    from . import devices, recordings
    now = now or int(time.time())
    planned = 0
    for interest in interests(enabled_only=True):
        device_id = interest.get("record_device_id")
        if not device_id:
            continue
        dev = db.q1("SELECT * FROM devices WHERE id = ?", (device_id,))
        if not dev:
            continue
        scope = devices.scope_for(dev, False)
        for e in events(scope, interest["id"], now=now):
            if e["state"] != "upcoming" or e["start"] > now + 7 * 86400 or e["start"] < now + 60:
                continue
            key = f"rec:{device_id}:{e['title']}:{e['start']}"
            if db.q1("SELECT 1 FROM sport_done WHERE key = ?", (key,)):
                continue
            start = e["start"] - PAD_BEFORE_S
            end = min(e["end"] + PAD_AFTER_S, start + 6 * 3600)
            try:
                recordings.create(dev, scope, e["channel_id"], e["title"], start, end)
                planned += 1
            except (ValueError, LookupError) as exc:
                db.log_event("sport_record_failed", {"title": e["title"], "error": str(exc)}, device_id=device_id)
            _done(key)
    return planned
