"""Hushållet: Lovisas profil och inbjudan, samt vem som går före när bara en ström är ledig.

- En profil "Lovisa" skapas automatiskt (ingen vuxenläge, ingen kod). Hon ser allt i vanligt läge.
- En inbjudan är en åttasiffrig kod som gäller i 14 dagar och kan användas tre gånger. Den som anger koden i appen får en enhet
  som är *låst* till profilen: servern svarar alltid som den profilen, vad appen än skickar. Enheten får också använda
  telefonen utanför hemmet (Nabu Casa) om det är inställt. En ny kod skapas automatiskt när den gamla gått ut eller tagit slut.
- Prioritet: varje profil har en prioritet (ägaren 100, andra 0). Toucan tillåter bara en ström åt gången. Gäller bara enheter som är
  låsta till en profil (som Lovisas telefon): börjar någon med högre prioritet titta stoppas hennes ström direkt (med förklaring),
  och hon kan inte ta över. Mellan vanliga boxar gäller som förut frågan "X tittar redan, ta över?".
"""
import hmac
import secrets
import time

from . import db, profiles

PERSON = "Lovisa"
INVITE_KEY = "family_invite"
INVITE_DAYS = 14
INVITE_USES = 3


def _invite():
    data = db.get_setting(INVITE_KEY)
    return data if isinstance(data, dict) else None


def ensure():
    """Skapar profilen och en giltig inbjudan om de saknas. Körs vid start och när sidan visas."""
    row = db.q1("SELECT id FROM viewers WHERE lower(name) = lower(?)", (PERSON,))
    if row is None:
        created = profiles.create({"name": PERSON, "allow_adult": False, "emoji": "🙂"})
        pid = created["id"]
    else:
        pid = row["id"]
    inv = _invite()
    if not inv or inv.get("profile_id") != pid or inv["expires"] < time.time() or inv["uses_left"] <= 0:
        new_invite(pid)
    return pid


def new_invite(profile_id=None):
    pid = profile_id or (db.q1("SELECT id FROM viewers WHERE lower(name) = lower(?)", (PERSON,)) or {"id": None})["id"]
    if pid is None:
        return ensure() and _invite()
    inv = {"code": f"{secrets.randbelow(100_000_000):08d}", "profile_id": pid, "name": PERSON,
           "expires": int(time.time()) + INVITE_DAYS * 86400, "uses_left": INVITE_USES}
    db.set_setting(INVITE_KEY, inv)
    return inv


def take_invite(code):
    """Förbrukar en användning om koden stämmer. Anropas inuti parningens transaktion."""
    inv = _invite()
    code = str(code or "").strip()
    if not inv or len(code) != 8 or not hmac.compare_digest(inv["code"], code):
        return None
    if inv["expires"] < time.time() or inv["uses_left"] <= 0:
        return None
    if not profiles.get(inv["profile_id"]):
        return None
    inv["uses_left"] -= 1
    db.set_setting(INVITE_KEY, inv)
    return dict(inv)


def view():
    inv = _invite()
    if not inv:
        ensure()
        inv = _invite()
    ok = inv["expires"] >= time.time() and inv["uses_left"] > 0
    return {"name": inv["name"], "code": inv["code"] if ok else None, "expires": inv["expires"],
            "uses_left": inv["uses_left"], "valid": ok}


# ---------------------------------------------------------------- prioritet

def _effective_profile_id(device_id, profile_id=None):
    row = db.q1("SELECT locked_profile_id, default_profile_id FROM devices WHERE id = ?", (device_id,))
    if row and row["locked_profile_id"]:
        return row["locked_profile_id"]
    if profile_id:
        return profile_id
    if row and row["default_profile_id"]:
        return row["default_profile_id"]
    return profiles.owner_id()


def is_locked(device_id):
    row = db.q1("SELECT locked_profile_id FROM devices WHERE id = ?", (device_id,))
    return bool(row and row["locked_profile_id"])


def priority_for(device_id, profile_id=None):
    p = profiles.get(_effective_profile_id(device_id, profile_id))
    return p["priority"] if p else 0


def person_for(device_id, profile_id=None):
    p = profiles.get(_effective_profile_id(device_id, profile_id))
    return p["name"] if p and p["name"] else None
