"""Inbjudan: profilen hittas via id (inte namn), så att en befintlig, omdöpt profil behålls vid uppdatering (utan nätverk)."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-fam-")

from hub import db, family, profiles  # noqa: E402

db.connect()
fails = 0


def check(name, cond, info=None):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    fails += 0 if cond else 1


# En äldre installation: profilen har ett eget namn och inbjudan pekar på den (inget sparat id).
old = profiles.create({"name": "Partner", "allow_adult": False})
db.set_setting(family.INVITE_KEY, {"code": "12345678", "profile_id": old["id"], "name": "Partner",
                                   "expires": int(time.time()) - 10, "uses_left": 0})
before = len(profiles.all_profiles())
pid = family.ensure()
check("befintlig profil behålls, ingen ny skapas", pid == old["id"] and len(profiles.all_profiles()) == before, (pid, old["id"]))
check("id:t sparas", db.get_setting(family.PROFILE_KEY) == old["id"])
v = family.view()
check("namnet kommer från profilen", v["name"] == "Partner" and v["valid"] and len(v["code"]) == 8, v)
profiles.update(old["id"], {"name": "Nytt namn"})
check("omdöpt profil följer med", family.view()["name"] == "Nytt namn" and family.ensure() == old["id"])
inv = family.take_invite(v["code"])
check("koden gäller en enhet", inv is not None and family.take_invite(v["code"]) is None)
check("koden gäller ett dygn", 86000 < family._invite()["expires"] - time.time() <= 86400 + 5)

print("ALLT OK" if fails == 0 else f"{fails} FEL")
sys.exit(1 if fails else 0)
