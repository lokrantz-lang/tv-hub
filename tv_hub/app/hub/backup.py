"""Säkerhetskopia av inställningar (profiler, snabbval, kategorier, sport, boxinställningar).

Innehåller aldrig koder (PIN), nycklar, token eller portaluppgifter. Import slår ihop med det som finns."""
import json
import time

from . import VERSION, db, reminders, sports

FORMAT = "tvhub-settings"
SETTING_KEYS = ("start_categories", "browse_options", "ai_provider", "ai_url", "ai_model", "ai_nightly")
DEVICE_FIELDS = ("allow_adult", "pin_required", "revert_hours", "adult_idle_minutes", "remote_allowed")
MAX_ROWS = 2000


def export_all():
    settings = {k: db.get_setting(k) for k in SETTING_KEYS if db.get_setting(k) is not None}
    notify = reminders.settings()
    settings["reminder_settings"] = {"notify": notify.get("notify", "")}
    sport = dict(sports.settings())
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM devices")}
    sport["notify_devices"] = [names[i] for i in sport.get("notify_devices", []) if i in names]
    settings["sport_settings"] = sport
    profiles = [
        {"name": r["name"], "position": r["position"], "rules": r["rules"], "color": r["color"], "emoji": r["emoji"],
         "allow_adult": bool(r["allow_adult"]), "guest": bool(r["guest"])}
        for r in db.q("SELECT * FROM viewers ORDER BY position, id")]
    pname = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM viewers")}
    devices = []
    for r in db.q("SELECT * FROM devices WHERE model != 'integration' ORDER BY id"):
        item = {"name": r["name"]}
        for f in DEVICE_FIELDS:
            item[f] = r[f]
        item["default_profile"] = pname.get(r["default_profile_id"])
        devices.append(item)
    return {
        "format": FORMAT, "version": 1, "app": VERSION, "exported": db.iso(),
        "settings": settings,
        "profiles": profiles,
        "devices": devices,
        "saved_filters": [{"name": r["name"], "type": r["type"], "adult": r["adult"], "params": r["params"]}
                          for r in db.q("SELECT * FROM saved_filters ORDER BY id")],
        "category_prefs": [dict(r) for r in db.q("SELECT * FROM category_prefs")],
        "profile_category_prefs": [
            {"profile": pname.get(r["profile_id"]), "type": r["type"], "category_id": r["category_id"],
             "hidden": r["hidden"], "position": r["position"]}
            for r in db.q("SELECT * FROM profile_category_prefs") if r["profile_id"] in pname],
        "sport_interests": [
            {k: r[k] for k in ("name", "kind", "keywords", "exclude", "title_only", "notify", "position", "enabled")}
            | {"record_device": names.get(r["record_device_id"])}
            for r in db.q("SELECT * FROM sport_interests ORDER BY position, id")],
    }


def _check(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Filen är inte en säkerhetskopia från TV Hub.")
    if int(data.get("version") or 0) != 1:
        raise ValueError("Okänd version av säkerhetskopian.")
    for key in ("profiles", "devices", "saved_filters", "category_prefs", "sport_interests", "profile_category_prefs"):
        value = data.get(key, [])
        if not isinstance(value, list) or len(value) > MAX_ROWS:
            raise ValueError(f"Ogiltigt innehåll i {key}.")
        if not all(isinstance(v, dict) for v in value):
            raise ValueError(f"Ogiltigt innehåll i {key}.")
    if not isinstance(data.get("settings", {}), dict):
        raise ValueError("Ogiltiga inställningar.")


def import_all(data):
    """Slår ihop kopian med nuvarande inställningar. Returnerar antal poster per del."""
    _check(data)
    done = {"profiler": 0, "boxar": 0, "snabbval": 0, "kategorier": 0, "sport": 0, "inställningar": 0}
    with db.tx() as conn:
        for p in data.get("profiles", []):
            name = str(p.get("name") or "").strip()[:30]
            if not name:
                continue
            rules = p.get("rules")
            if not isinstance(rules, str):
                rules = json.dumps(rules if isinstance(rules, dict) else {})
            vals = (int(p.get("position") or 0), rules, str(p.get("color") or "")[:9] or None,
                    str(p.get("emoji") or "")[:4] or None, 1 if p.get("allow_adult") else 0, 1 if p.get("guest") else 0)
            row = conn.execute("SELECT id FROM viewers WHERE lower(name) = lower(?)", (name,)).fetchone()
            if row:
                conn.execute("UPDATE viewers SET position=?, rules=?, color=?, emoji=?, allow_adult=?, guest=? WHERE id=?",
                             vals + (row["id"],))
            else:
                conn.execute("""INSERT INTO viewers (name, position, rules, color, emoji, allow_adult, guest, created)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (name,) + vals + (db.iso(),))
            done["profiler"] += 1
        for d in data.get("devices", []):
            row = conn.execute("SELECT id FROM devices WHERE name = ?", (str(d.get("name") or ""),)).fetchone()
            if not row:
                continue
            profile = conn.execute("SELECT id FROM viewers WHERE lower(name) = lower(?)",
                                   (str(d.get("default_profile") or ""),)).fetchone()
            hours = max(0, min(48, int(d.get("revert_hours") or 0)))
            idle = max(1, min(600, int(d.get("adult_idle_minutes") or 15)))
            conn.execute("""UPDATE devices SET allow_adult=?, pin_required=?, revert_hours=?, adult_idle_minutes=?,
                            remote_allowed=?, default_profile_id=? WHERE id=?""",
                         (1 if d.get("allow_adult") else 0, 1 if d.get("pin_required") else 0, hours, idle,
                          1 if d.get("remote_allowed") else 0, profile["id"] if profile else None, row["id"]))
            done["boxar"] += 1
        for f in data.get("saved_filters", []):
            name, kind = str(f.get("name") or "")[:60], str(f.get("type") or "")[:20]
            params = f.get("params") if isinstance(f.get("params"), str) else json.dumps(f.get("params") or {})
            if not name or not kind:
                continue
            adult = 1 if f.get("adult") else 0
            if not conn.execute("SELECT 1 FROM saved_filters WHERE name=? AND type=? AND adult=?",
                                (name, kind, adult)).fetchone():
                conn.execute("INSERT INTO saved_filters (name, type, adult, params, created) VALUES (?,?,?,?,?)",
                             (name, kind, adult, params, db.iso()))
                done["snabbval"] += 1
        for c in data.get("category_prefs", []):
            if not c.get("type") or c.get("category_id") is None:
                continue
            conn.execute("""INSERT OR REPLACE INTO category_prefs
                            (type, category_id, hidden, position, adult_override, display_name, skip_all)
                            VALUES (?,?,?,?,?,?,?)""",
                         (str(c["type"]), str(c["category_id"]), 1 if c.get("hidden") else 0, c.get("position"),
                          c.get("adult_override"), c.get("display_name"), c.get("skip_all")))
            done["kategorier"] += 1
        for c in data.get("profile_category_prefs", []):
            row = conn.execute("SELECT id FROM viewers WHERE lower(name) = lower(?)", (str(c.get("profile") or ""),)).fetchone()
            if not row or not c.get("type") or c.get("category_id") is None:
                continue
            conn.execute("INSERT OR REPLACE INTO profile_category_prefs (profile_id, type, category_id, hidden, position) "
                         "VALUES (?,?,?,?,?)", (row["id"], str(c["type"]), str(c["category_id"]),
                                                1 if c.get("hidden") else 0, c.get("position")))
            done["kategorier"] += 1
        for s in data.get("sport_interests", []):
            name = str(s.get("name") or "")[:60]
            if not name or conn.execute("SELECT 1 FROM sport_interests WHERE lower(name)=lower(?)", (name,)).fetchone():
                continue
            rec = conn.execute("SELECT id FROM devices WHERE name = ?", (str(s.get("record_device") or ""),)).fetchone()
            conn.execute("""INSERT INTO sport_interests (name, kind, keywords, exclude, title_only, notify,
                            record_device_id, position, enabled, created) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (name, str(s.get("kind") or "sport")[:20], str(s.get("keywords") or "[]"),
                          str(s.get("exclude") or "[]"), 1 if s.get("title_only") else 0, 1 if s.get("notify") else 0,
                          rec["id"] if rec else None, int(s.get("position") or 0), 0 if s.get("enabled") == 0 else 1,
                          db.iso()))
            done["sport"] += 1
    settings = data.get("settings", {})
    for key in SETTING_KEYS:
        if key in settings:
            db.set_setting(key, settings[key])
            done["inställningar"] += 1
    if isinstance(settings.get("reminder_settings"), dict):
        reminders.save_settings(settings["reminder_settings"])
        done["inställningar"] += 1
    sp = settings.get("sport_settings")
    if isinstance(sp, dict):
        sp = dict(sp)
        names = sp.pop("notify_devices", [])
        ids = [r["id"] for r in db.q("SELECT id, name FROM devices") if r["name"] in names]
        sp["notify_devices"] = ids
        sports.save_settings(sp)
        done["inställningar"] += 1
    db.log_event("backup_import", done)
    return done
