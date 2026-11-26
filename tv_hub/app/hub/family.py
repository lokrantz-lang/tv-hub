"""Hushållet: en inbjuden persons profil och inbjudan, samt vem som går före när bara en ström är ledig.

- En profil skapas automatiskt för den som bjuds in (ingen vuxenläge, ingen kod) och ser allt i vanligt läge. Den heter
  "Inbjuden" från början; ägaren döper om den i webbadminen (namnet sparas bara i databasen, aldrig i koden).
- En inbjudan är en åttasiffrig kod som gäller ett dygn och för en enhet. Den som anger koden i appen får en enhet som är
  *låst* till profilen: servern svarar alltid som den profilen, vad appen än skickar.
- Prioritet: varje profil har en prioritet (ägaren 100, andra 0). Toucan tillåter bara en ström åt gången. Gäller bara enheter som är
  låsta till en profil: börjar någon med högre prioritet titta stoppas den låsta enhetens ström direkt (med förklaring),
  och den kan inte ta över. Mellan vanliga boxar gäller som förut frågan "X tittar redan, ta över?".
"""
import hmac
import secrets
import time

from . import db, profiles

DEFAULT_NAME = "Inbjuden"   # standardnamn; ägaren byter namn i webbadminen
PROFILE_KEY = "family_profile_id"
INVITE_KEY = "family_invite"
INVITE_HOURS = 24   # en kod gäller ett dygn
INVITE_USES = 1     # och för en enhet; därefter skapas en ny


def _invite():
    data = db.get_setting(INVITE_KEY)
    return data if isinstance(data, dict) else None


def profile_id():
    """Profilen för den inbjudna. Sparas som id (inte namn), så att den kan döpas om fritt.
    Äldre installationer: id:t finns i den sparade inbjudan."""
    pid = db.get_setting(PROFILE_KEY)
    if pid and profiles.get(pid):
        return int(pid)
    inv = _invite()
    if inv and inv.get("profile_id") and profiles.get(inv["profile_id"]):
        db.set_setting(PROFILE_KEY, int(inv["profile_id"]))
        return int(inv["profile_id"])
    return None


def ensure():
    """Skapar profilen och en giltig inbjudan om de saknas. Körs vid start och när sidan visas."""
    pid = profile_id()
    if pid is None:
        created = profiles.create({"name": DEFAULT_NAME, "allow_adult": False, "emoji": "🙂"})
        pid = created["id"]
        db.set_setting(PROFILE_KEY, int(pid))
    inv = _invite()
    if not inv or inv.get("profile_id") != pid or inv["expires"] < time.time() or inv["uses_left"] <= 0:
        new_invite(pid)
    return pid


def person_name():
    p = profiles.get(profile_id()) if profile_id() else None
    return p["name"] if p else DEFAULT_NAME


def new_invite(pid_arg=None):
    pid = pid_arg or profile_id()
    if pid is None:
        return ensure() and _invite()
    inv = {"code": f"{secrets.randbelow(100_000_000):08d}", "profile_id": pid,
           "expires": int(time.time()) + INVITE_HOURS * 3600, "uses_left": INVITE_USES}
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
    return {"name": person_name(), "code": inv["code"] if ok else None, "expires": inv["expires"],
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
