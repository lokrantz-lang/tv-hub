"""Mina program: snabbval överst på Hem, per profil (t.ex. Rapport, TV4 Nyheterna, Robinson).

Sätt:
- latest:  senaste sändningen (direkt om den sänds nu, annars den senaste ur arkivet)
- main:    senaste huvudsändningen (bara sändningar som börjar vid de angivna tiderna, t.ex. 19:00 och 22:00)
- archive: avsnitt ur kanalens arkiv i tur och ordning: påbörjat avsnitt först, annars nästa efter det senast
           sedda, annars det senaste
- recent:  de två senaste huvudsändningarna (längsta sändningen per dag ur programguiden), du väljer själv
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
    "recent": "De två senaste sändningarna (du väljer)",
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


_NOISE = {"se", "sv", "swe", "hd", "fhd", "uhd", "sd", "4k", "hevc", "raw", "50fps", "fps", "vip", "sweden", "sverige"}


def _words(text):
    return re.sub(r"[^a-z0-9åäö]+", " ", str(text or "").casefold()).split()


def channel_score(name, target):
    """Hur väl kanalnamnet motsvarar målet (t.ex. SVT1, TV4), eller None. Hela namnet måste stämma: CCTV4 är inte TV4 och
    TV4 Film är inte TV4. Landsprefix och kvalitetsord (SE:, HD, FHD) räknas bort."""
    t = "".join(_words(target))
    words = _words(name)
    core = [w for w in words if w not in _NOISE]
    if "".join(core) == t:
        return (3, "se" in words or "sv" in words or "swe" in words, "hd" in words or "fhd" in words, -len(name))
    # Namnet kan vara skrivet med mellanslag ("SVT 1"): sammanfogat måste det bli exakt målet.
    return None


def _channel(name_patterns, need_archive=False):
    """Hitta en svensk kanal efter namn (t.ex. SVT1, TV4). Föredrar svenska prefix och HD."""
    best = None
    for pattern in name_patterns:
        for item in catalog.search(pattern, "live", limit=60, scope=NORMAL):
            if need_archive and int(item.get("archive_days") or 0) <= 0:
                continue
            score = channel_score(item["name"], pattern)
            if score is None:
                continue
            score = score + (int(item.get("archive_days") or 0) > 0,)
            if best is None or score > best[0]:
                best = (score, item)
    return best[1] if best else None


def suggest(profile_id):
    """Förslagen Rapport, TV4 Nyheterna (19:00/22:00) och Robinson, om kanalerna finns."""
    created = []
    existing = {(_norm(p["query"]), p["channel_id"]) for p in for_profile(profile_id)}
    svt1 = _channel(["SVT1"])
    svt2 = _channel(["SVT2"], need_archive=True)  # Aktuellt kräver arkiv för att gå att se i efterhand
    tv4 = _channel(["TV4"])
    wanted = []
    if svt1:
        wanted.append({"name": "Rapport", "mode": "recent", "channel_id": svt1["id"], "query": "Rapport"})
    if svt2:
        wanted.append({"name": "Aktuellt", "mode": "latest", "channel_id": svt2["id"], "query": "Aktuellt"})
    if tv4:
        wanted.append({"name": "TV4 Nyheterna", "mode": "recent", "channel_id": tv4["id"], "query": "TV4 Nyheterna"})
        wanted.append({"name": "Robinson", "mode": "archive", "channel_id": tv4["id"], "query": "Robinson"})
    for w in wanted:
        if (_norm(w["query"]), w["channel_id"]) in existing:
            continue
        created.append(create(dict(w, profile_id=profile_id)))
    return created


def repair_wrong_channel():
    """En gång: förslag som av misstag hamnade på CCTV4 (kinesiska) i stället för TV4 flyttas till rätt kanal."""
    if db.get_setting("myprog_repaired_cctv"):
        return
    tv4 = _channel(["TV4"])
    if tv4:
        for r in db.q("SELECT id, channel_id FROM my_programs WHERE name IN ('TV4 Nyheterna', 'Robinson')"):
            ch = catalog.get_item("live", r["channel_id"]) if r["channel_id"] else None
            if ch and "cctv" in "".join(_words(ch["name"])):
                db.x("UPDATE my_programs SET channel_id = ? WHERE id = ?", (tv4["id"], r["id"]))
        db.set_setting("myprog_repaired_cctv", "1")


def convert_to_recent():
    """En gång (2.9.7): de förslagna Rapport och TV4 Nyheterna byter till "två senaste, du väljer"."""
    if db.get_setting("myprog_recent_v297"):
        return
    db.x("""UPDATE my_programs SET mode = 'recent', times = '[]'
            WHERE name IN ('Rapport', 'TV4 Nyheterna') AND mode IN ('latest', 'main')""")
    db.set_setting("myprog_recent_v297", "1")


def ensure_defaults():
    """Lägger in förslagen (Rapport, Aktuellt, TV4 Nyheterna, Robinson) en gång för varje profil som inte har några
    snabbval, så att de finns utan att man behöver hitta i webbadminen. Har man tagit bort dem läggs de inte tillbaka."""
    from . import profiles
    for p in profiles.all_profiles():
        if p["guest"] or db.get_setting(f"myprog_seeded_{p['id']}"):
            continue
        if for_profile(p["id"]):
            db.set_setting(f"myprog_seeded_{p['id']}", "1")
            continue
        if suggest(p["id"]):
            db.set_setting(f"myprog_seeded_{p['id']}", "1")


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
        if row["mode"] == "recent":
            # Huvudsändningen = längsta sändningen med namnet varje dag (Rapport 19:30, inte kortversionerna).
            by_day = {}
            for p in past:
                key = time.strftime("%Y-%m-%d", time.localtime(p["start"]))
                cur = by_day.get(key)
                if cur is None or (p["end"] - p["start"], p["start"]) >= (cur["end"] - cur["start"], cur["start"]):
                    by_day[key] = p
            picks = sorted(by_day.values(), key=lambda p: p["start"], reverse=True)[:2]
            if picks:
                out["choices"] = []
                for p in picks:
                    seen, pos = extras.catchup_state(profile_id, row["channel_id"], p["start"])
                    out["choices"].append({
                        "label": f"{_day(p['start']).capitalize()} {_clock(p['start'])}",
                        "subtitle": "✓ sedd" if seen else "",
                        "command": _catchup_cmd(row, channel, p, 0 if seen else pos)})
                out["command"] = out["choices"][0]["command"]  # äldre appar spelar den senaste direkt
                out["subtitle"] = "Välj: " + " eller ".join(c["label"] for c in out["choices"])
                return out
            if live:
                out["command"] = {"action": "play", "type": "live", "id": str(row["channel_id"]), "name": channel["name"],
                                  "icon": channel.get("icon")}
                out["subtitle"] = f"Sänds nu · {_clock(live['start'])}"
                return out
            raise LookupError("Finns inte i arkivet just nu." if guide["archive_days"] else "Kanalen har inget arkiv.")
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
