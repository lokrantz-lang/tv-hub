"""Världscupen i hoppning: planering, historik och kontroll mot Toucan.

Kalendern för säsongen 2026/27 (Longines FEI Jumping World Cup, Western European League och finalen i Göteborg) är
inlagd här, hämtad från FEI, TT och TV4. Vad som faktiskt går att se kommer ur *din* programguide och kanallista:

  - Varje etapp får sina sändningar ur programguiden (titeln måste handla om hoppning och världscupen eller etappens ort).
  - Status per sändning: sänds nu / kommande / finns i arkivet. Knappar: Titta, Spela in (kommande) och Ladda ner
    (redan sänt, ur kanalens arkiv). Spela in och Ladda ner skapar en vanlig inspelning på boxen.
  - Kontroll: vilka kanaler i Toucan-listan som kan bära sändningarna (TV4, Sjuan, TV4 Sportkanalen, SVT, Eurosport …),
    om de har arkiv och om programguiden täcker dem.
  - Historik: programguiden sparar bara en vecka, så sändningar som setts sparas här så att tidigare etapper
    fortfarande visar kanal och tid. Resultat (vinnare) kan läggas in i webbadminen.

TV4-bolaget har rättigheterna till världscupen i hoppning fr.o.m. säsongen 2026/27 och lägger sändningarna på Sjuan, TV4 Sportkanalen och
TV4 Play Plus Sport (SVT delar bara finalen). Kanalerna per etapp kommer från offentliga tablåer och är inte verifierade mot ett Toucan-konto. Tider och datum kan ändras av arrangörerna; tider som saknas visas som "ej fastställd".
"""
import json
import re
import time

from . import db, sports, sportsets
from .catalog import Scope

NORMAL = Scope(adult=0)
SEASON = "2026/27"
INTEREST_NAME = "Världscupen i hoppning"
SEEN_KEY = "worldcup_seen"
RESULTS_KEY = "worldcup_results"
PAD_BEFORE_S = 120
PAD_AFTER_S = 15 * 60
MAX_SEEN_PER_LEG = 12
MAX_AHEAD_S = 14 * 86400 - 3600

# shift = timmar att lägga till etappens lokala tid för att få svensk tid (London är en timme efter, Helsingfors före).
LEGS = [
    {"id": "oslo", "name": "Oslo", "country": "NOR", "start": "2026-10-07", "end": "2026-10-11",
     "wc": ("2026-10-11", "15:30"), "shift": 0, "kind": "league",
     "winner": "Max Kühner (AUT)", "horse": "EIC Up Too Jacco Blue", "tv": "TV4"},
    {"id": "lyon", "name": "Lyon", "country": "FRA", "start": "2026-10-27", "end": "2026-11-01",
     "wc": ("2026-11-01", "15:30"), "shift": 0, "kind": "league",
     "winner": "Julien Epaillard (FRA)", "horse": "Le Coultre de Muze", "tv": "TV4"},
    {"id": "verona", "name": "Verona", "country": "ITA", "start": "2026-11-05", "end": "2026-11-08",
     "wc": ("2026-11-08", "14:30"), "shift": 0, "kind": "league", "tv": "Sjuan (14:15–17:45)", "slot": ("Sjuan", "14:15", "17:45")},
    {"id": "stuttgart", "name": "Stuttgart", "country": "GER", "start": "2026-11-11", "end": "2026-11-15",
     "wc": ("2026-11-15", "15:10"), "shift": 0, "kind": "league", "tv": "Sjuan (14:45–17:40)", "slot": ("Sjuan", "14:45", "17:40")},
    {"id": "madrid", "name": "Madrid", "country": "ESP", "start": "2026-11-26", "end": "2026-11-29",
     "wc": ("2026-11-29", "15:00"), "shift": 0, "kind": "league", "tv": "Sjuan (14:55–17:40) och TV4 Play Plus Sport", "slot": ("Sjuan", "14:55", "17:40")},
    {"id": "acoruna", "name": "A Coruña", "country": "ESP", "start": "2026-12-03", "end": "2026-12-06",
     "wc": ("2026-12-06", "16:30"), "shift": 0, "kind": "league", "tv": "TV4 Sportkanalen (16:45–19:45) och TV4 Play Plus Sport", "slot": ("TV4 Sportkanalen", "16:45", "19:45")},
    {"id": "london", "name": "London", "country": "GBR", "start": "2026-12-16", "end": "2026-12-21",
     "wc": ("2026-12-20", "14:35"), "shift": 1, "kind": "league", "tv": "TV4"},
    {"id": "mechelen", "name": "Mechelen", "country": "BEL", "start": "2026-12-26", "end": "2026-12-30",
     "wc": ("2026-12-30", "15:00"), "shift": 0, "kind": "league", "tv": "TV4"},
    {"id": "basel", "name": "Basel", "country": "SUI", "start": "2027-01-06", "end": "2027-01-10",
     "wc": ("2027-01-10", "14:15"), "shift": 0, "kind": "league"},
    {"id": "leipzig", "name": "Leipzig", "country": "GER", "start": "2027-01-13", "end": "2027-01-17",
     "wc": None, "shift": 0, "kind": "league"},
    {"id": "amsterdam", "name": "Amsterdam", "country": "NED", "start": "2027-01-28", "end": "2027-01-31",
     "wc": None, "shift": 0, "kind": "league", "preliminary": True},
    {"id": "bordeaux", "name": "Bordeaux", "country": "FRA", "start": "2027-02-04", "end": "2027-02-07",
     "wc": None, "shift": 0, "kind": "league", "preliminary": True},
    {"id": "helsinki", "name": "Helsingfors", "country": "FIN", "start": "2027-03-04", "end": "2027-03-07",
     "wc": None, "shift": 0, "kind": "league", "preliminary": True},
    {"id": "final", "name": "Final i Göteborg", "city": "Göteborg", "country": "SWE", "start": "2027-03-23", "end": "2027-03-28",
     "wc": None, "shift": 0, "kind": "final", "tv": "TV4 och SVT (delas)"},
    {"id": "sihs", "name": "Sweden International Horse Show", "country": "SWE", "start": "2026-12-03", "end": "2026-12-06",
     "wc": ("2026-12-06", "17:00"), "shift": 0, "kind": "other", "tv": "TV4 Play Plus Sport",
     "note": "Stockholm, Strawberry Arena. H&M Grand Prix söndag. Inte en världscupsetapp."},
]
LEG_BY_ID = {leg["id"]: leg for leg in LEGS}

HOPP = ("hoppning", "hasthoppning", "show jumping", "showjumping", "jumping")
WORLD_CUP = ("varldscupen", "varldscup", "varldscupfinalen", "varldscupfinal", "world cup", "worldcup", "longines")
SIHS = ("sweden international horse show", "sihs", "stockholm international horse show")
EXCLUDE = ("trav", "galopp", "dressyr", "dressage", "horse racing", "ponny")


def _n(text):
    """Normaliserad text utan diakriter, så att "världscupen" och "varldscupen" blir lika."""
    return " " + sports.norm(re.sub(r"[åä]", "a", re.sub(r"ö", "o", str(text or "").casefold()))) + " "


def _has(text, phrases):
    return any(f" {p} " in text or p in text.split() for p in phrases)


def _ts(day, clock, shift=0):
    y, m, d = (int(x) for x in day.split("-"))
    hh, mm = (int(x) for x in clock.split(":"))
    return int(time.mktime((y, m, d, hh, mm, 0, 0, 0, -1))) + shift * 3600


def _bounds(leg):
    return _ts(leg["start"], "00:00") - 86400, _ts(leg["end"], "00:00") + 3 * 86400


def wc_time(leg):
    """Världscupklassens starttid (epok, svensk tid), eller None om den inte är fastställd."""
    if not leg.get("wc"):
        return None
    return _ts(leg["wc"][0], leg["wc"][1], leg.get("shift", 0))


def _leg_match(leg, title, description):
    text = _n(f"{title} {description or ''}")
    if _has(text, [_n(x).strip() for x in EXCLUDE]) and not _has(text, [_n(x).strip() for x in HOPP]):
        return False
    if leg["kind"] == "other":
        return _has(text, [_n(x).strip() for x in SIHS])
    hopp = _has(text, [_n(x).strip() for x in HOPP])
    wc = _has(text, [_n(x).strip() for x in WORLD_CUP])
    city = _n(leg.get("city") or leg["name"]).strip()
    if leg["kind"] == "final":
        return hopp and wc and (f" {city} " in text or _has(text, ["final", "finalen", "varldscupfinalen", "varldscupfinal"]))
    return hopp and (wc or f" {city} " in text)


# ---------------------------------------------------------------- sändningar per etapp

def _leg_rows(leg, now):
    lo, hi = _bounds(leg)
    rows = db.q("SELECT * FROM epg WHERE end > ? AND start < ? ORDER BY start", (max(lo, now - sports.KEEP_BACK_S), hi))
    return [r for r in rows if _leg_match(leg, r["title"], r["description"])]


def _seen():
    data = db.get_setting(SEEN_KEY)
    return data if isinstance(data, dict) else {}


def _remember(leg, events):
    """Sparar vad programguiden visat, eftersom den bara minns en vecka."""
    if not events:
        return
    seen = _seen()
    cur = {(s["channel"], s["start"]): s for s in seen.get(leg["id"], [])}
    changed = False
    for e in events:
        key = (e["channel"], e["start"])
        item = {"channel": e["channel"], "start": e["start"], "end": e["end"], "title": e["title"]}
        if cur.get(key) != item:
            cur[key] = item
            changed = True
    if changed:
        seen[leg["id"]] = sorted(cur.values(), key=lambda s: s["start"])[-MAX_SEEN_PER_LEG:]
        db.set_setting(SEEN_KEY, seen)


def _clock(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _recording_for(recs, event):
    for r in recs:
        if str(r["stream_id"]) == str(event["channel_id"]) and event["start"] - 600 <= r["start"] <= event["end"]:
            return {"id": r["id"], "status": r["status"], "size_bytes": r.get("size_bytes")}
    return None


def _record_request(event, now):
    """Vad knappen Spela in/Ladda ner ska skicka. Kommande/pågående: spela in. Redan sänt: ladda ner ur arkivet."""
    if event["state"] == "past":
        return {"action": "download", "label": "Ladda ner", "body": {
            "stream_id": event["channel_id"], "title": event["title"], "start": event["start"], "end": event["end"]}}
    start = event["start"] - PAD_BEFORE_S if event["state"] == "upcoming" else event["start"]
    end = min(event["end"] + PAD_AFTER_S, start + 6 * 3600)
    if end > now + MAX_AHEAD_S:  # inspelningar kan planeras högst två veckor framåt
        return {"action": "later", "label": "Går att spela in två veckor innan", "body": None}
    return {"action": "record", "label": "Spela in", "body": {
        "stream_id": event["channel_id"], "title": event["title"], "start": start, "end": end}}


def _main_event(events):
    if not events:
        return None
    return max(events, key=lambda e: (e["end"] - e["start"], e["start"]))


def _slot_event(leg, now):
    """Sändning enligt offentlig tablå, med kanalen från din egen lista. Används när guiden saknar sändningen."""
    slot, wc = leg.get("slot"), leg.get("wc")
    if not slot or not wc:
        return None
    label, a, b = slot
    spec = next((w for w in CHANNEL_WANTS if w[0] == label), None)
    if spec is None:
        return None
    start, end = _ts(wc[0], a), _ts(wc[0], b)
    chans = _find_channels(spec[2], spec[3])
    if not chans:
        return None
    state = "now" if start <= now < end else ("past" if end <= now else "upcoming")
    ch = chans[0]
    if state == "past":
        playable = [c for c in chans if int(c["archive_days"] or 0) * 86400 >= now - start]
        if not playable:
            return None
        ch = playable[0]
    title = f"Världscupen i hoppning: {leg['name']}"
    event = {"id": f"{ch['id']}:{start}", "title": title, "description": "", "start": start, "end": end, "state": state,
             "channel_id": ch["id"], "channel": ch["name"], "icon": ch.get("icon"), "also": [], "interests": [],
             "when": sports.when_label(start, end), "unconfirmed": True, "ids": [c["id"] for c in chans]}
    if state == "now":
        event["command"] = {"action": "play", "type": "live", "id": str(ch["id"]), "name": ch["name"], "icon": ch.get("icon")}
    elif state == "past":
        event["command"] = {"action": "play", "type": "catchup", "id": str(ch["id"]),
                            "name": f"{ch['name']}: {title} {_clock(start)}", "catchup_start": int(start),
                            "catchup_minutes": max(1, int((end - start) // 60)), "icon": ch.get("icon")}
    return event


def leg_view(leg, scope, now, recs=None):
    results = db.get_setting(RESULTS_KEY) or {}
    manual = results.get(leg["id"]) or {}
    winner = manual.get("winner") or leg.get("winner")
    horse = manual.get("horse") or leg.get("horse")
    wc = wc_time(leg)
    lo, hi = _bounds(leg)
    start_ts, end_ts = _ts(leg["start"], "00:00"), _ts(leg["end"], "23:59")
    events = sports.build_events(_leg_rows(leg, now), scope, None, now)
    _remember(leg, events)
    if scope.adult == 0:
        planned = _slot_event(leg, now)
        if planned and not any(abs(e["start"] - planned["start"]) < 5400 for e in events):
            events = sorted(events + [planned], key=lambda e: e["start"])
    state = "upcoming"
    done_ts = wc + 4 * 3600 if wc else end_ts + 6 * 3600  # klar några timmar efter världscupklassen
    if now > done_ts:
        state = "done"
    elif now >= start_ts:
        state = "ongoing"
    main = next((e for e in events if e.get("unconfirmed")), None) or _main_event(events)
    out_events = []
    for e in events:
        item = dict(e)
        item["main"] = bool(main and e["id"] == main["id"])
        item.pop("ids", None)
        item["record"] = _record_request(e, now)
        item["recording"] = _recording_for(recs, e) if recs else None
        item["play_label"] = {"now": "Titta nu", "past": "Titta i efterhand"}.get(e["state"])
        out_events.append(item)
    seen = [s for s in _seen().get(leg["id"], [])
            if not any(abs(s["start"] - e["start"]) < 60 and s["channel"] == e["channel"] for e in events)]
    seen_view = [{"channel": s["channel"], "title": s["title"], "start": s["start"], "end": s["end"],
                  "when": sports.when_label(s["start"])} for s in seen]
    if events and all(e.get("unconfirmed") for e in events):
        headline, level = "Enligt tablå (ej bekräftad i din programguide)", "warn"
    elif events:
        headline = {"now": "Sänds nu", "upcoming": "Sändning finns i guiden", "past": "Finns i arkivet"}[
            "now" if any(e["state"] == "now" for e in events) else
            "upcoming" if any(e["state"] == "upcoming" for e in events) else "past"]
        level = "ok"
    elif state == "done":
        headline, level = ("Sändes, men finns inte längre i arkivet" if seen_view else "Finns inte i arkivet"), "muted"
    else:
        headline, level = "Ingen sändning i programguiden ännu", "warn"
    return {
        "id": leg["id"], "name": leg["name"], "country": leg["country"], "kind": leg["kind"],
        "dates": f"{leg['start'][8:]}/{leg['start'][5:7]}–{leg['end'][8:]}/{leg['end'][5:7]}",
        "start": start_ts, "end": end_ts, "state": state,
        "wc_time": wc, "wc_when": sports.when_label(wc) if wc else "Tid ej fastställd",
        "wc_label": (f"{sports.when_label(wc)}" if wc else "Tid ej fastställd"),
        "preliminary": bool(leg.get("preliminary")), "note": leg.get("note"),
        "tv": leg.get("tv"), "winner": winner, "horse": horse,
        "events": out_events, "seen": seen_view, "headline": headline, "level": level,
    }


def remember_all(now=None):
    """Housekeeping: spara det programguiden visar, även om ingen öppnar sidan."""
    now = int(now or time.time())
    for leg in LEGS:
        _remember(leg, sports.build_events(_leg_rows(leg, now), NORMAL, None, now))


def overview(scope=NORMAL, device=None, now=None):
    now = int(now or time.time())
    recs = None
    if device is not None:
        from . import recordings
        recs = recordings.for_device(device["id"], adult=0)
    legs = [leg_view(leg, scope, now, recs) for leg in LEGS]
    league = [leg for leg in legs if leg["kind"] != "other"]
    upcoming = [leg for leg in league if leg["state"] != "done"]
    nxt = upcoming[0] if upcoming else None
    return {
        "season": SEASON, "legs": legs, "next": nxt["id"] if nxt else None,
        "now": [e for leg in legs for e in leg["events"] if e["state"] == "now"],
        "rights": "TV4 har världscupen i hoppning från säsongen 2026/27. SVT visar finalen och mästerskapen tillsammans med TV4.",
        "icon": "🏇",
    }


# ---------------------------------------------------------------- kontroll mot Toucan

CHANNEL_WANTS = [
    ("TV4", True, [r"\btv ?4\b"], [r"film|fakta|guld|hockey|fotboll|sport|play|news|nyheterna|komedi|extra|live|xtra"],
     "Huvudkanalen för världscupen."),
    ("Sjuan", True, [r"\bsjuan\b"], [], "Verona, Stuttgart och Madrid sänds på Sjuan enligt offentlig tablå. Utan den missar du dem."),
    ("TV4 Sportkanalen", True, [r"\btv ?4 ?(play )?sport", r"\btv ?4 ?sportkanalen", r"\bsportkanalen\b"], [],
     "A Coruña (6/12) sänds på Sportkanalen enligt offentlig tablå."),
    ("SVT", False, [r"\bsvt ?(1|2|24)\b"], [], "Bara världscupfinalen och mästerskapen."),
    ("Eurosport", False, [r"\beurosport"], [], "Kan visa hoppning i stora tävlingar."),
    ("Horse & Country", False, [r"\bhorse\b.*\bcountry\b", r"\bh ?& ?c\b", r"\bhorse ?(&|and) ?country\b"], [],
     "Hästkanal, visar ibland etapper."),
    ("FEI TV / ClipMyHorse", False, [r"\bfei ?tv\b", r"\bclipmyhorse"], [], "Här streamas alla etapper, men det är en egen tjänst."),
]


def _find_channels(include, exclude):
    rows = db.q("""SELECT item_id AS id, name, archive_days, epg_id, icon FROM items
                   WHERE type = 'live' AND adult = 0 ORDER BY name""")
    out = []
    for r in rows:
        name = sportsets._norm(r["name"])
        if any(re.search(p, name) for p in include) and not any(re.search(p, name) for p in exclude):
            out.append(dict(r))
    out.sort(key=lambda c: (not sports.SWEDISH_NAME.search(c["name"] or ""), int(c["archive_days"] or 0) <= 0,
                            "hd" not in (c["name"] or "").casefold(), -int(c["archive_days"] or 0), len(c["name"])))
    return out


def check(now=None):
    """Vad finns i Toucan-listan och programguiden för världscupen? Klartext för webbadminen."""
    now = int(now or time.time())
    status = sports.status()
    items = []
    for label, required, include, exclude, why in CHANNEL_WANTS:
        found = _find_channels(include, exclude)[:6]
        chans = []
        for c in found:
            key = sports._epg_key(c)
            guide = db.q1("SELECT COUNT(*) AS n FROM epg WHERE epg_key = ? AND end > ?", (key, now))["n"]
            chans.append({"id": c["id"], "name": c["name"], "archive_days": int(c["archive_days"] or 0),
                          "guide_programs": guide})
        archive = any(c["archive_days"] > 0 for c in chans)
        guide_ok = any(c["guide_programs"] > 0 for c in chans)
        if not chans:
            level, text = ("bad" if required else "muted"), "Finns inte i din Toucan-lista."
        else:
            bits = [f"{len(chans)} kanal{'er' if len(chans) > 1 else ''} hittad{'e' if len(chans) > 1 else ''}",
                    "arkiv finns" if archive else "inget arkiv (går inte att ladda ner i efterhand)",
                    "programguiden läses" if guide_ok else "programguiden saknas för den (kanalen skannas inte än)"]
            level = "ok" if (archive and guide_ok) else "warn"
            text = ", ".join(bits) + "."
        items.append({"label": label, "required": required, "level": level, "text": text, "why": why, "channels": chans})
    programs = 0
    channels_with = set()
    for leg in LEGS:
        for r in _leg_rows(leg, now):
            programs += 1
            channels_with.add(r["epg_key"])
    notes = []
    if not status.get("last_scan"):
        notes.append({"level": "warn", "text": "Programguiden har inte skannats än. Starta en skanning under Sport."})
    if programs == 0:
        notes.append({"level": "warn", "text": "Programguiden innehåller inga världscupssändningar just nu. TV4 har publicerat "
                      "tider bara en bit framåt, och guiden fylls på i takt med att Toucan får in dem."})
    else:
        notes.append({"level": "ok", "text": f"Programguiden har {programs} program om världscupen, på {len(channels_with)} guidekanal(er)."})
    return {"items": items, "notes": notes, "programs": programs, "guide": {
        "last_scan": status.get("last_scan"), "channels": status.get("channels"), "programs": status.get("programs")}}


# ---------------------------------------------------------------- inställningar

def set_result(leg_id, winner, horse):
    if leg_id not in LEG_BY_ID:
        raise LookupError("Okänd etapp.")
    results = db.get_setting(RESULTS_KEY) or {}
    winner = str(winner or "").strip()[:80]
    horse = str(horse or "").strip()[:80]
    if winner or horse:
        results[leg_id] = {"winner": winner, "horse": horse}
    else:
        results.pop(leg_id, None)
    db.set_setting(RESULTS_KEY, results)
    return results.get(leg_id) or {}


def interest():
    row = db.q1("SELECT id FROM sport_interests WHERE name = ?", (INTEREST_NAME,))
    return sports.get_interest(row["id"]) if row else None


def ensure_interest():
    """Sportintresset som ger notis när en sändning börjar och kan spela in alla automatiskt. Skapas en gång."""
    if db.get_setting("worldcup_interest_v1"):
        return
    if interest() is None:
        sports.create_interest({
            "name": INTEREST_NAME, "kind": "sport", "title_only": False, "notify": True,
            "keywords": ["världscupen + hoppning", "världscupen + hästhoppning", "world cup + jumping", "longines + hoppning",
                         "sweden international horse show"],
            "exclude": ["dressyr", "trav", "galopp"]})
    db.set_setting("worldcup_interest_v1", "1")


def auto_record(device_id):
    """Spela in alla världscupssändningar automatiskt på en box (None = av)."""
    ensure_interest()
    it = interest()
    if it is None:
        raise LookupError("Intresset saknas.")
    sports.update_interest(it["id"], {"record_device_id": device_id or None})
    return {"record_device_id": (interest() or {}).get("record_device_id")}


def settings_view():
    it = interest()
    return {"notify": bool(it and it["notify"]), "record_device_id": it["record_device_id"] if it else None}
