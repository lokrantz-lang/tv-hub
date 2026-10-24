"""Profiler: en per person, plus t.ex. Tillsammans och Gäst (samma som Filmkvällens tittarlägen).

Varje profil har egna favoriter, Fortsätt titta, sedda, senaste kanaler och tummar.
- allow_adult: profilen får slå på vuxenläget (dessutom krävs boxens PIN om boxen kräver den).
- guest: inget sparas (inga favoriter, inget Fortsätt titta).
- PIN (valfri): appen frågar efter koden när man byter till profilen.

Boxen skickar vald profil som X-TVHub-Profile. Utan den används boxens standardprofil, annars den första profilen.
"""
import hashlib
import hmac
import secrets

from . import db

COLORS = ["#3B82F6", "#EC4899", "#10B981", "#F59E0B", "#8B5CF6", "#EF4444", "#14B8A6", "#6B7280"]


def _row(r):
    if r is None:
        return None
    keys = r.keys()
    return {
        "id": r["id"], "name": r["name"], "position": r["position"],
        "color": (r["color"] if "color" in keys else None) or COLORS[r["id"] % len(COLORS)],
        "emoji": r["emoji"] if "emoji" in keys else None,
        "has_pin": bool(r["pin_hash"]) if "pin_hash" in keys else False,
        "allow_adult": bool(r["allow_adult"]) if "allow_adult" in keys else False,
        "guest": bool(r["guest"]) if "guest" in keys else False,
    }


def all_profiles():
    return [_row(r) for r in db.q("SELECT * FROM viewers ORDER BY position, id")]


def get(profile_id):
    try:
        return _row(db.q1("SELECT * FROM viewers WHERE id = ?", (int(profile_id),)))
    except (TypeError, ValueError):
        return None


def owner_id():
    """Ägarens profil (den första som får använda vuxenläget). Gamla data och webbadminens favoriter hör till den."""
    row = db.q1("SELECT id FROM viewers WHERE allow_adult = 1 ORDER BY id LIMIT 1") or db.q1("SELECT MIN(id) AS id FROM viewers")
    return row["id"] if row and row["id"] is not None else 0


def resolve(value, device=None):
    """Profilen för ett anrop: X-TVHub-Profile, annars boxens standardprofil, annars ägaren."""
    p = get(value) if value not in (None, "") else None
    if p is None and device is not None:
        keys = device.keys() if hasattr(device, "keys") else []
        if "default_profile_id" in keys and device["default_profile_id"]:
            p = get(device["default_profile_id"])
    return p or get(owner_id()) or {"id": 0, "name": "", "allow_adult": True, "guest": False, "has_pin": False}


def _hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()


def _clean(data, current=None):
    name = str(data.get("name", current["name"] if current else "") or "").strip()[:30]
    if not name:
        raise ValueError("Namn saknas.")
    clash = db.q1("SELECT id FROM viewers WHERE lower(name) = lower(?)", (name,))
    if clash and (current is None or clash["id"] != current["id"]):
        raise ValueError("Det finns redan en profil med det namnet.")
    color = str(data.get("color") or (current or {}).get("color") or "")[:9] or None
    emoji = str(data.get("emoji") or "")[:4] or None if "emoji" in data else (current or {}).get("emoji")
    return {
        "name": name, "color": color, "emoji": emoji,
        "allow_adult": 1 if data.get("allow_adult", (current or {}).get("allow_adult")) else 0,
        "guest": 1 if data.get("guest", (current or {}).get("guest")) else 0,
    }


def create(data):
    v = _clean(data)
    pos = (db.q1("SELECT MAX(position) AS m FROM viewers")["m"] or 0) + 1
    cur = db.x("""INSERT INTO viewers (name, position, rules, created, color, emoji, allow_adult, guest)
                  VALUES (?, ?, '{}', ?, ?, ?, ?, ?)""",
               (v["name"], pos, db.iso(), v["color"], v["emoji"], v["allow_adult"], v["guest"]))
    if data.get("pin"):
        set_pin(cur.lastrowid, data["pin"])
    return get(cur.lastrowid)


def update(profile_id, data):
    current = get(profile_id)
    if not current:
        raise LookupError("Profilen finns inte.")
    v = _clean(data, current)
    db.x("UPDATE viewers SET name = ?, color = ?, emoji = ?, allow_adult = ?, guest = ? WHERE id = ?",
         (v["name"], v["color"], v["emoji"], v["allow_adult"], v["guest"], current["id"]))
    if "pin" in data:
        set_pin(current["id"], data.get("pin"))
    if "position" in data:
        db.x("UPDATE viewers SET position = ? WHERE id = ?", (int(data["position"]), current["id"]))
    return get(current["id"])


def delete(profile_id):
    p = get(profile_id)
    if not p:
        raise LookupError("Profilen finns inte.")
    if len(all_profiles()) <= 1:
        raise ValueError("Den sista profilen kan inte tas bort.")
    if p["allow_adult"] and db.q1("SELECT COUNT(*) AS n FROM viewers WHERE allow_adult = 1")["n"] <= 1:
        raise ValueError("Minst en profil måste få använda vuxenläget. Ge en annan profil det först.")
    pid = p["id"]
    with db.tx() as conn:
        for table in ("favorites", "progress", "watched", "fav_episodes", "my_programs"):
            conn.execute(f"DELETE FROM {table} WHERE profile_id = ?", (pid,))
        for table in ("ratings", "hidden_picks", "ai_picks"):
            conn.execute(f"DELETE FROM {table} WHERE viewer_id = ?", (pid,))
        # Allt som hör till profilen tas bort, också sett-logg, önskelista och sessionshistorik.
        conn.execute("DELETE FROM seen_log WHERE viewer_id = ?", (pid,))
        conn.execute("DELETE FROM wishlist WHERE viewer_id = ?", (pid,))
        conn.execute("DELETE FROM sessions WHERE viewer_id = ?", (pid,))
        conn.execute("UPDATE devices SET default_profile_id = NULL WHERE default_profile_id = ?", (pid,))
        conn.execute("DELETE FROM viewers WHERE id = ?", (pid,))
    return True


def set_pin(profile_id, pin):
    pin = str(pin or "").strip()
    if not pin:
        db.x("UPDATE viewers SET pin_salt = NULL, pin_hash = NULL WHERE id = ?", (int(profile_id),))
        return False
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("Koden ska vara 4–8 siffror.")
    salt = secrets.token_hex(16)
    db.x("UPDATE viewers SET pin_salt = ?, pin_hash = ? WHERE id = ?", (salt, _hash(pin, salt), int(profile_id)))
    return True


def check_pin(profile_id, pin):
    row = db.q1("SELECT pin_salt, pin_hash FROM viewers WHERE id = ?", (int(profile_id),))
    if not row:
        raise LookupError("Profilen finns inte.")
    if not row["pin_hash"]:
        return True
    return hmac.compare_digest(_hash(str(pin or ""), row["pin_salt"]), row["pin_hash"])
