"""Mina program: snabbval överst på Hem, per profil (t.ex. Rapport, TV4 Nyheterna, Robinson).

Sätt:
- latest:  senaste sändningen (direkt om den sänds nu, annars den senaste ur arkivet)
- main:    senaste huvudsändningen (bara sändningar som börjar vid de angivna tiderna, t.ex. 19:00 och 22:00)
- archive: avsnitt ur kanalens arkiv i tur och ordning: påbörjat avsnitt först, annars nästa efter det senast
           sedda, annars det senaste
- series:  en serie i Toucans bibliotek: fortsätt där du var
"""
import json
import re
import time

from . import catalog, db, extras
from .catalog import Scope

MODES = {
    "latest": "Senaste sändningen",
    "main": "Senaste huvudsändningen",
    "archive": "Avsnitt ur arkivet i tur och ordning",
    "series": "Serie: fortsätt där du var",
}
MATCH_MINUTES = 20  # huvudsändningen får börja så här mycket före eller efter angiven tid
NORMAL = Scope(adult=0)


def _norm(text):
    return re.sub(r"[^a-z0-9åäö]", "", str(text or "").casefold())


def _row(r):
    item = dict(r)
    try:
        item["times"] = json.loads(item.get("times") or "[]")
    except ValueError:
        item["times"] = []
    item["mode_label"] = MODES.get(item["mode"], item["mode"])
    if item.get("channel_id"):
        ch = catalog.get_item("live", item["channel_id"])
        item["channel_name"] = ch["name"] if ch else None
        item["icon"] = ch.get("icon") if ch else None
    if item.get("series_id"):
        se = catalog.get_item("series", item["series_id"])
        item["series_name"] = se["name"] if se else None
        item["icon"] = se.get("icon") if se else None
    return item


def for_profile(profile_id):
    return [_row(r) for r in db.q("SELECT * FROM my_programs WHERE profile_id = ? ORDER BY position, id", (int(profile_id),))]


def _clean(data):
    from . import profiles
    if not profiles.get(data.get("profile_id")):
        raise LookupError("Profilen finns inte.")
    mode = data.get("mode")
    if mode not in MODES:
        raise ValueError("Välj vad som ska spelas.")
    name = str(data.get("name") or "").strip()[:40]
    query = str(data.get("query") or "").strip()[:80]
    channel_id = str(data.get("channel_id") or "").strip() or None
    series_id = str(data.get("series_id") or "").strip() or None
    times = []
    for t in data.get("times") or []:
        m = re.match(r"^(\d{1,2})[:.](\d{2})$", str(t).strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"Ogiltig tid: {t} (skriv t.ex. 19:00).")
        times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    if mode == "series":
        se = catalog.get_item("series", series_id) if series_id else None
        if not se:
            raise ValueError("Välj serien.")
        if se.get("adult"):
            raise ValueError("Vuxeninnehåll kan inte läggas som snabbval.")
        name = name or catalog.get_item("series", series_id)["name"]
    else:
        ch = catalog.get_item("live", channel_id) if channel_id else None
        if not ch:
            raise ValueError("Välj kanal.")
        if ch.get("adult"):
            raise ValueError("Vuxeninnehåll kan inte läggas som snabbval.")
        if not query:
            raise ValueError("Ange programmets namn som det står i programguiden.")
        if mode == "main" and not times:
            raise ValueError("Ange minst en tid för huvudsändningen, t.ex. 19:00.")
        name = name or query
    return {"profile_id": int(data["profile_id"]), "name": name, "mode": mode, "channel_id": channel_id,
            "query": query or None, "times": json.dumps(times), "series_id": series_id}


def create(data):
    v = _clean(data)
    pos = (db.q1("SELECT MAX(position) AS m FROM my_programs WHERE profile_id = ?", (v["profile_id"],))["m"] or 0) + 1
    cur = db.x("""INSERT INTO my_programs (profile_id, name, mode, channel_id, query, times, series_id, position, created)
                  VALUES (?,?,?,?,?,?,?,?,?)""",
               (v["profile_id"], v["name"], v["mode"], v["channel_id"], v["query"], v["times"], v["series_id"], pos, db.iso()))
    return _row(db.q1("SELECT * FROM my_programs WHERE id = ?", (cur.lastrowid,)))


def update(program_id, data):
    row = db.q1("SELECT * FROM my_programs WHERE id = ?", (int(program_id),))
    if not row:
        raise LookupError("Finns inte.")
    merged = dict(row)
    merged["times"] = json.loads(row["times"] or "[]")
    merged.update(data)
    v = _clean(merged)
    db.x("""UPDATE my_programs SET profile_id = ?, name = ?, mode = ?, channel_id = ?, query = ?, times = ?, series_id = ?
            WHERE id = ?""", (v["profile_id"], v["name"], v["mode"], v["channel_id"], v["query"], v["times"], v["series_id"], row["id"]))
    if "position" in data:
        db.x("UPDATE my_programs SET position = ? WHERE id = ?", (int(data["position"]), row["id"]))
    return _row(db.q1("SELECT * FROM my_programs WHERE id = ?", (row["id"],)))


def delete(program_id):
    return db.x("DELETE FROM my_programs WHERE id = ?", (int(program_id),)).rowcount > 0


def _channel(name_patterns):
    """Hitta en svensk kanal efter namn (t.ex. SVT1, TV4). Föredrar HD och svenska kategorier."""
    best = None
    for pattern in name_patterns:
        for item in catalog.search(pattern, "live", limit=30, scope=NORMAL):
            n = _norm(item["name"])
            target = _norm(pattern)
            if target not in n:
                continue
            score = (n.endswith(target) or n.endswith(target + "hd") or ("se" + target) in n, "hd" in n, -len(n))
            if best is None or score > best[0]:
                best = (score, item)
    return best[1] if best else None


def suggest(profile_id):
    """Förslagen Rapport, TV4 Nyheterna (19:00/22:00) och Robinson, om kanalerna finns."""
    created = []
    existing = {(_norm(p["query"]), p["channel_id"]) for p in for_profile(profile_id)}
    svt1 = _channel(["SVT1"])
    tv4 = _channel(["TV4"])
    wanted = []
    if svt1:
        wanted.append({"name": "Rapport", "mode": "latest", "channel_id": svt1["id"], "query": "Rapport"})
    if tv4:
        wanted.append({"name": "TV4 Nyheterna", "mode": "main", "channel_id": tv4["id"], "query": "TV4 Nyheterna",
                       "times": ["19:00", "22:00"]})
        wanted.append({"name": "Robinson", "mode": "archive", "channel_id": tv4["id"], "query": "Robinson"})
    for w in wanted:
        if (_norm(w["query"]), w["channel_id"]) in existing:
            continue
        created.append(create(dict(w, profile_id=profile_id)))
    return created


# ---------------------------------------------------------------- vad ska spelas

def _clock(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _day(ts):
    lt, now = time.localtime(ts), time.localtime()
    if lt.tm_yday == now.tm_yday and lt.tm_year == now.tm_year:
        return "idag"
    yday = time.localtime(time.time() - 86400)
    if lt.tm_yday == yday.tm_yday and lt.tm_year == yday.tm_year:
        return "igår"
    return f"{lt.tm_mday}/{lt.tm_mon}"


def _at_times(start, times):
    lt = time.localtime(start)
    minutes = lt.tm_hour * 60 + lt.tm_min
    for t in times:
        h, m = (int(x) for x in t.split(":"))
        if abs(minutes - (h * 60 + m)) <= MATCH_MINUTES:
            return True
    return False


def _catchup_cmd(row, channel, p, position_ms=0):
    minutes = max(1, int((p["end"] - p["start"]) // 60))
    return {"action": "play", "type": "catchup", "id": str(row["channel_id"]),
            "name": f"{channel['name']}: {p['title']} {_clock(p['start'])}", "catchup_start": int(p["start"]),
            "catchup_minutes": minutes, "icon": channel.get("icon"), "start_ms": int(position_ms or 0)}


def resolve(row, profile_id):
    """Ett snabbval -> {"title", "subtitle", "icon", "command"} eller {"title", "subtitle", "error"}."""
    out = {"id": row["id"], "title": row["name"], "icon": row.get("icon")}
    try:
        if row["mode"] == "series":
            from .control import _series_play
            cmd = _series_play(row["series_id"], row.get("series_name"), profile_id=profile_id)
            out["command"] = cmd
            out["subtitle"] = cmd["name"].replace(row.get("series_name") or "", "").strip() or "Fortsätt"
            return out
        from .server import guide_for
        channel = catalog.get_item("live", row["channel_id"])
        if not channel:
            raise LookupError("Kanalen finns inte längre.")
        guide = guide_for(row["channel_id"])
        q = _norm(row["query"])
        now = time.time()
        matches = [p for p in guide["programs"] if q and q in _norm(p.get("title"))]
        if row["mode"] == "main":
            matches = [p for p in matches if _at_times(p["start"], row["times"])]
        live = next((p for p in matches if p["now"]), None)
        past = sorted([p for p in matches if p["end"] <= now and p["playable"]], key=lambda p: p["start"])
        if row["mode"] in ("latest", "main"):
            if live and row["mode"] == "latest":
                out["command"] = {"action": "play", "type": "live", "id": str(row["channel_id"]), "name": channel["name"],
                                  "icon": channel.get("icon")}
                out["subtitle"] = f"Sänds nu · {_clock(live['start'])}"
                return out
            if past:
                p = past[-1]
                seen, pos = extras.catchup_state(profile_id, row["channel_id"], p["start"])
                out["command"] = _catchup_cmd(row, channel, p, 0 if seen else pos)
                out["subtitle"] = f"{_day(p['start']).capitalize()} {_clock(p['start'])}" + (" · ✓ sedd" if seen else "")
                return out
            if live:
                out["command"] = {"action": "play", "type": "live", "id": str(row["channel_id"]), "name": channel["name"],
                                  "icon": channel.get("icon")}
                out["subtitle"] = f"Sänds nu · {_clock(live['start'])}"
                return out
            raise LookupError("Finns inte i arkivet just nu." if guide["archive_days"] else "Kanalen har inget arkiv.")
        # archive: i tur och ordning
        if not past:
            if live:
                out["command"] = {"action": "play", "type": "live", "id": str(row["channel_id"]), "name": channel["name"],
                                  "icon": channel.get("icon")}
                out["subtitle"] = "Sänds nu"
                return out
            raise LookupError("Inget avsnitt i arkivet just nu." if guide["archive_days"] else "Kanalen har inget arkiv.")
        states = [(p, *extras.catchup_state(profile_id, row["channel_id"], p["start"])) for p in past]
        started = [x for x in states if not x[1] and x[2] > 0]
        if started:
            p, _, pos = started[-1]
            out["command"] = _catchup_cmd(row, channel, p, pos)
            out["subtitle"] = f"{_day(p['start']).capitalize()} {_clock(p['start'])} · fortsätt"
            return out
        last_seen = max((i for i, x in enumerate(states) if x[1]), default=-1)
        if last_seen >= 0 and last_seen + 1 < len(states):
            p = states[last_seen + 1][0]
        elif last_seen >= 0:
            p = states[-1][0]
            out["subtitle"] = f"Alla sedda · senaste {_day(p['start'])} {_clock(p['start'])}"
            out["command"] = _catchup_cmd(row, channel, p, 0)
            return out
        else:
            p = states[-1][0]
        out["command"] = _catchup_cmd(row, channel, p, 0)
        out["subtitle"] = f"{_day(p['start']).capitalize()} {_clock(p['start'])}"
        return out
    except Exception as exc:  # noqa: BLE001 - ett snabbval som inte går visas med förklaring
        out["error"] = str(exc) or "Går inte att spela just nu."
        out["subtitle"] = out["error"]
        return out
