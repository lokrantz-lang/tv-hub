"""Enhetstest av avbrottsnotiserna (utan nätverk). Kör: python3 tests/test_alerts.py"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-alerts-")

from hub import alerts, db, portals  # noqa: E402

db.connect()
bad = []


def check(name, cond, info=None):
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    if not cond:
        bad.append(name)


db.x("INSERT INTO portals (name, url, username, password, priority, enabled, created) VALUES ('P', 'http://x', 'u', 'p', 1, 1, ?)", (db.iso(),))
alerts.save_settings({"box_hours": 24})
sent = []
alerts.check_once(send=lambda t, m: sent.append((t, m)))
check("inget skickas när allt är lugnt", sent == [], sent)

real = portals.summary
portals.summary = lambda p: {"consecutive_failures": 5}
out = alerts.check_once(send=lambda t, m: None)
check("leverantören nere ger en notis", len(out) == 1 and "svarar inte" in out[0][0], out)
check("ingen ny notis medan den är nere", alerts.check_once(send=lambda t, m: None) == [])
portals.summary = lambda p: {"consecutive_failures": 0}
out = alerts.check_once(send=lambda t, m: None)
check("notis när den svarar igen", len(out) == 1 and "igen" in out[0][0], out)
portals.summary = real

db.log_event("recording_failed", {"title": "18+", "size": 0, "error": "x"})
db.log_event("recording_done", {"title": "Hemligt", "size": 1})
out = alerts.check_once(send=lambda t, m: None)
check("misslyckad inspelning ger en notis utan titel", len(out) == 1 and "Hemligt" not in str(out) and "18+" not in str(out), out)
check("samma händelse ger inte notis igen", alerts.check_once(send=lambda t, m: None) == [])
alerts.save_settings({"recording": False})
db.log_event("recording_failed", {"title": "x"})
check("avstängd inspelningsnotis tystar", alerts.check_once(send=lambda t, m: None) == [])

old = db.iso(db.now().replace(year=db.now().year))
db.x("INSERT INTO devices (name, model, token_hash, created, last_seen) VALUES ('Sovrum', 'Alpha', 'h', ?, ?)",
     (old, db.iso()))
check("aktiv box ger ingen notis", alerts.check_once(send=lambda t, m: None) == [])
stale = db.iso(db.now().fromtimestamp(time.time() - 3 * 86400, db.now().tzinfo))
db.x("UPDATE devices SET last_seen = ?", (stale,))
out = alerts.check_once(send=lambda t, m: None)
check("tyst box ger en notis", len(out) == 1 and "Sovrum" in out[0][1], out)
check("tyst box repeteras inte direkt", alerts.check_once(send=lambda t, m: None) == [])
alerts.save_settings({"box_hours": 0})
alerts._state["boxes"].clear()
check("av = ingen boxnotis", alerts.check_once(send=lambda t, m: None) == [])
try:
    alerts.save_settings({"box_hours": 99999})
    check("orimlig tid nekas", False)
except ValueError:
    check("orimlig tid nekas", True)

# abonnemangsvarning
pid = db.q1("SELECT id FROM portals")["id"]
alerts.save_settings({"expiry_days": 7, "box_hours": 0, "recording": True})
t0 = time.time()
db.x("INSERT INTO checks (portal_id, ts, tcp_ok, api_ok, exp_date) VALUES (?, ?, 1, 1, ?)", (pid, db.iso(), int(t0 + 3 * 86400)))
out = alerts.check_once(send=lambda t, m: None, now=t0)
check("abonnemang som snart går ut ger en notis", len(out) == 1 and "abonnemanget" in out[0][0] and "dagar" in out[0][1], out)
check("varningen repeteras inte inom ett dygn", alerts.check_once(send=lambda t, m: None, now=t0 + 3600) == [])
out = alerts.check_once(send=lambda t, m: None, now=t0 + 90000)
check("varningen kommer igen nästa dygn", len(out) == 1, out)
db.x("INSERT INTO checks (portal_id, ts, tcp_ok, api_ok, exp_date) VALUES (?, ?, 1, 1, ?)", (pid, db.iso(), int(t0 + 60 * 86400)))
alerts._state["expiry"] = 0.0
check("långt kvar ger ingen varning", alerts.check_once(send=lambda t, m: None, now=t0) == [])
alerts.save_settings({"expiry_days": 0})
db.x("INSERT INTO checks (portal_id, ts, tcp_ok, api_ok, exp_date) VALUES (?, ?, 1, 1, ?)", (pid, db.iso(), int(t0 + 86400)))
check("av = ingen abonnemangsvarning", alerts.check_once(send=lambda t, m: None, now=t0) == [])
try:
    alerts.save_settings({"expiry_days": 500})
    check("orimligt antal dagar nekas", False)
except ValueError:
    check("orimligt antal dagar nekas", True)

print("\nALLT OK" if not bad else f"\n{len(bad)} FEL: {', '.join(bad)}")
sys.exit(1 if bad else 0)
