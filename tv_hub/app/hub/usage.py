"""Översikt: tittad tid och uppskattad datamängd per dag, per box och per profil, samt historik per profil.

Datamängden är en UPPSKATTNING (tid × antagen bitrate); källan rapporterar ingen verklig trafik.
Underlaget är sessionerna och sparas bara så länge som retention_days (30 dagar som standard).
- Inspelningar räknas inte (de tittas i efterhand och är inte ny trafik från källan).
- Vuxenläget räknas bara som tid under "Övrigt" i profilfördelningen, aldrig med titlar och aldrig i historiken.
- Gäst visas som en egen rad utan historik.
"""
from datetime import datetime, timedelta

from . import db, profiles
from .config import CFG

RECORD_TYPES = ("record", "record_archive", "record_vod")
DEFAULT_BITRATES = {"live": 4.0, "film": 6.0, "series": 4.0}  # Mbit/s
MAX_SESSION_S = 12 * 3600  # en session som aldrig avslutades räknas inte längre än så
OTHER = "Övrigt"
GUEST = "Gäst"


def bitrates():
    cur = dict(DEFAULT_BITRATES)
    saved = db.get_setting("usage_bitrates")
    if isinstance(saved, dict):
        for k in cur:
            try:
                if float(saved.get(k, 0)) > 0:
                    cur[k] = float(saved[k])
            except (TypeError, ValueError):
                pass
    return cur


def set_bitrates(data):
    cur = bitrates()
    for k in cur:
        if k in data:
            try:
                v = float(data[k])
            except (TypeError, ValueError):
                raise ValueError("Ange ett tal i Mbit/s.")
            if not 0.5 <= v <= 80:
                raise ValueError("Ange mellan 0,5 och 80 Mbit/s.")
            cur[k] = round(v, 1)
    db.set_setting("usage_bitrates", cur)
    return cur


def _kind(session_type):
    if session_type == "vod":
        return "film"
    if session_type in ("episode", "series"):
        return "series"
    return "live"  # live och catch-up


def _local(value):
    dt = db.parse_iso(value)
    return dt.astimezone() if dt else None


def _rows(days):
    cutoff = db.ago(days=days)
    out = []
    for r in db.q(
            """SELECT s.id, s.device_id, s.type, s.item_name, s.started, s.ended, s.last_heartbeat, s.adult, s.viewer_id,
                      d.name AS device_name
               FROM sessions s LEFT JOIN devices d ON d.id = s.device_id
               WHERE s.started >= ? AND s.type NOT IN ('record', 'record_archive', 'record_vod')
               ORDER BY s.started""", (cutoff,)):
        start = _local(r["started"])
        end = _local(r["ended"] or r["last_heartbeat"])
        if start is None or end is None:
            continue
        secs = max(0, min(int((end - start).total_seconds()), MAX_SESSION_S))
        if secs < 5:
            continue
        out.append({"start": start, "secs": secs, "kind": _kind(r["type"]), "adult": bool(r["adult"]),
                    "device": r["device_name"] or f"Box {r['device_id']}", "viewer_id": r["viewer_id"],
                    "title": r["item_name"] or ""})
    return out


def _mb(secs, kind, rates):
    return secs * rates[kind] / 8  # Mbit/s × s / 8 = MB


def overview(days=30):
    days = max(1, min(int(days), 90))
    rates = bitrates()
    names = {p["id"]: p["name"] for p in profiles.all_profiles()}
    guests = {p["id"] for p in profiles.all_profiles() if p["guest"]}
    by_day = {}
    totals = {"secs": 0, "mb": 0.0, "boxes": {}, "profiles": {}}
    for r in _rows(days):
        day = r["start"].strftime("%Y-%m-%d")
        if r["adult"]:
            who = OTHER
        elif r["viewer_id"] is None or r["viewer_id"] in guests:
            who = GUEST
        else:
            who = names.get(r["viewer_id"], OTHER)
        mb = _mb(r["secs"], r["kind"], rates)
        d = by_day.setdefault(day, {"day": day, "secs": 0, "mb": 0.0, "boxes": {}, "profiles": {}, "kinds": {}})
        for bucket in (d, totals):
            bucket["secs"] += r["secs"]
            bucket["mb"] += mb
            for group, key in (("boxes", r["device"]), ("profiles", who)):
                cell = bucket[group].setdefault(key, {"secs": 0, "mb": 0.0})
                cell["secs"] += r["secs"]
                cell["mb"] += mb
        d["kinds"][r["kind"]] = d["kinds"].get(r["kind"], 0) + r["secs"]
    today = datetime.now().astimezone().date()
    series = []
    for i in range(days - 1, -1, -1):
        key = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        series.append(by_day.get(key) or {"day": key, "secs": 0, "mb": 0.0, "boxes": {}, "profiles": {}, "kinds": {}})
    return {"days": series, "totals": totals, "bitrates": rates,
            "estimate": True, "retention_days": CFG["retention_days"]}


def history(profile_id, day=None, limit=300):
    """Vad en profil har tittat på (titel, tid, längd). Aldrig vuxenläget, aldrig gäst, aldrig inspelningar."""
    prof = profiles.get(profile_id)
    if not prof or prof["guest"]:
        return {"profile": prof, "items": [], "note": "Gäst sparar ingen historik."}
    rows = []
    for r in _rows(90):
        if r["adult"] or r["viewer_id"] != prof["id"]:
            continue
        if day and r["start"].strftime("%Y-%m-%d") != day:
            continue
        rows.append({"title": r["title"], "type": r["kind"], "box": r["device"],
                     "day": r["start"].strftime("%Y-%m-%d"), "time": r["start"].strftime("%H:%M"), "secs": r["secs"]})
    rows.sort(key=lambda x: (x["day"], x["time"]), reverse=True)
    return {"profile": prof, "items": rows[:limit],
            "note": "Vuxenläget visas aldrig här, bara som tid under Övrigt i översikten."}
