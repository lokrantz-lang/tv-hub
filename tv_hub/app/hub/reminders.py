"""Påminnelser: "Påminn mig" på ett kommande program i programguiden.

När programmet börjar får boxen ett meddelande ("Startar nu … Byt kanal?") via samma väg som
fjärrstarten. Valfritt skickas också en notis till telefonen via en notify-tjänst i Home Assistant.
Påminnelser om vuxeninnehåll skickas aldrig till telefonen och syns bara i vuxenläget.
"""
import time

from . import catalog, control, db

LEAD_S = 30          # skickas så här långt innan programmet börjar
KEEP_S = 6 * 3600    # skickade påminnelser rensas efter programmet plus så här länge
FIELDS = "id, device_id, stream_id, channel, title, start, end, adult, sent"


def _row(r):
    item = dict(r)
    item["adult"] = bool(item["adult"])
    item["sent"] = bool(item["sent"])
    return item


def for_device(device_id, adult):
    rows = db.q(f"SELECT {FIELDS} FROM reminders WHERE device_id = ? AND adult = ? AND sent = 0 ORDER BY start",
                (int(device_id), 1 if adult else 0))
    return [_row(r) for r in rows]


def create(device, scope, stream_id, title, start, end):
    stream_id = str(stream_id or "")
    channel = catalog.get_item("live", stream_id)
    if not channel or not catalog.in_scope("live", stream_id, scope):
        raise LookupError("Kanalen finns inte.")
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        raise ValueError("Programtiden saknas.")
    if start <= time.time():
        raise ValueError("Programmet har redan börjat.")
    title = str(title or "").strip()[:200] or "Program"
    existing = db.q1("SELECT id FROM reminders WHERE device_id = ? AND stream_id = ? AND start = ? AND sent = 0",
                     (device["id"], stream_id, start))
    if existing:
        return _row(db.q1(f"SELECT {FIELDS} FROM reminders WHERE id = ?", (existing["id"],))), False
    adult = 1 if (scope.adult or channel.get("adult")) else 0
    cur = db.x("""INSERT INTO reminders (device_id, stream_id, channel, title, start, end, adult, created)
                  VALUES (?,?,?,?,?,?,?,?)""",
               (device["id"], stream_id, channel["name"], title, start, end, adult, db.iso()))
    return _row(db.q1(f"SELECT {FIELDS} FROM reminders WHERE id = ?", (cur.lastrowid,))), True


def delete(device, reminder_id):
    return db.x("DELETE FROM reminders WHERE id = ? AND device_id = ?", (int(reminder_id), device["id"])).rowcount > 0


# ---------- notis till telefonen ----------
def settings():
    return db.get_setting("reminder_settings") or {"notify": ""}


def save_settings(data):
    notify = str(data.get("notify") or "").strip()
    if notify.startswith("notify."):
        notify = notify[len("notify."):]
    if notify and not all(ch.isalnum() or ch == "_" for ch in notify):
        raise ValueError("Ogiltigt namn på notify-tjänsten.")
    value = {"notify": notify}
    db.set_setting("reminder_settings", value)
    return value


def notify_services():
    """Notify-tjänster i Home Assistant (t.ex. mobile_app_galaxy_s23_fe)."""
    try:
        services = control._core("GET", "services") or []
    except control.ControlError as exc:
        return {"available": False, "error": str(exc), "services": []}
    names = []
    for domain in services:
        if domain.get("domain") == "notify":
            names = sorted(k for k in (domain.get("services") or {}) if k not in ("notify", "send_message",
                                                                                  "persistent_notification"))
    return {"available": True, "services": names}


def _phone_notify(reminder, test=False):
    service = settings().get("notify")
    if not service:
        return False
    clock = time.strftime("%H:%M", time.localtime(reminder["start"]))
    body = {"title": "TV Hub" if test else f"Startar {clock}: {reminder['title']}",
            "message": "Testnotis från TV Hub." if test else f"{reminder['channel']}. Boxen frågar om den ska byta kanal."}
    control._core("POST", f"services/notify/{service}", body)
    return True


def test_notify():
    if not settings().get("notify"):
        raise ValueError("Välj en notify-tjänst först.")
    try:
        _phone_notify({"title": "", "channel": "", "start": time.time()}, test=True)
    except control.ControlError as exc:
        raise ValueError(str(exc))
    return {"ok": True}


# ---------- utskick ----------
def due(now=None):
    now = now or time.time()
    return [_row(r) for r in db.q(f"SELECT {FIELDS} FROM reminders WHERE sent = 0 AND start <= ?", (now + LEAD_S,))]


def send_due(now=None):
    now = now or time.time()
    sent = 0
    for r in due(now):
        db.x("UPDATE reminders SET sent = 1 WHERE id = ?", (r["id"],))
        if r["end"] <= now:
            continue  # boxen var avstängd hela programmet: skicka inget i efterhand
        control.enqueue(r["device_id"], {
            "action": "reminder", "name": "Påminnelse", "reminder_id": r["id"], "channel_id": r["stream_id"],
            "channel": r["channel"], "title": r["title"], "start": r["start"], "end": r["end"], "adult": r["adult"],
        })
        if not r["adult"]:
            try:
                _phone_notify(r)
            except Exception as exc:  # noqa: BLE001 - notisen får aldrig stoppa påminnelsen
                print(f"[fel] påminnelse till telefonen: {exc}", flush=True)
        sent += 1
    db.x("DELETE FROM reminders WHERE sent = 1 AND end < ?", (now - KEEP_S,))
    return sent


def loop(stop_event):
    while not stop_event.wait(15):
        try:
            send_due()
        except Exception as exc:  # noqa: BLE001
            print(f"[fel] påminnelser: {exc}", flush=True)
