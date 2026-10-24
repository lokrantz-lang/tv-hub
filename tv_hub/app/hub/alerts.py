"""Avbrottsnotiser till telefonen via Home Assistant: leverantören nere, misslyckad inspelning, box som tystnat."""
import time
from datetime import datetime, timezone

from . import control, db, portals, reminders

DEFAULTS = {"provider": True, "recording": True, "box_hours": 0}
DOWN_AFTER_FAILS = 3
BOX_REPEAT_S = 24 * 3600
_state = {"provider_down": False, "last_event": None, "boxes": {}}


def settings():
    out = dict(DEFAULTS)
    out.update(db.get_setting("alert_settings") or {})
    return out


def save_settings(data):
    cur = settings()
    for key in ("provider", "recording"):
        if key in data:
            cur[key] = bool(data[key])
    if "box_hours" in data:
        try:
            hours = int(data["box_hours"] or 0)
        except (TypeError, ValueError):
            raise ValueError("Timmar ska vara ett tal.")
        if not 0 <= hours <= 720:
            raise ValueError("Ange 0–720 timmar (0 = av).")
        cur["box_hours"] = hours
    db.set_setting("alert_settings", cur)
    return cur


def _send(title, message):
    service = reminders.settings().get("notify")
    if not service:
        return False
    try:
        control._core("POST", f"services/notify/{service}", {"title": title, "message": message})
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[fel] avbrottsnotis: {exc}", flush=True)
        return False


def test():
    if not reminders.settings().get("notify"):
        raise ValueError("Välj en notify-tjänst först (under Påminnelser).")
    if not _send("TV Hub", "Testnotis för avbrott."):
        raise ValueError("Det gick inte att skicka. Se loggen.")
    return {"ok": True}


def check_once(send=None, now=None):
    """Kollar alla villkor en gång. Returnerar de notiser som skickades (för tester)."""
    send = send or _send
    now = now or time.time()
    cfg = settings()
    sent = []

    def push(title, message):
        sent.append((title, message))
        send(title, message)

    if cfg["provider"]:
        enabled = [s for s in (portals.summary(p) for p in portals.all_rows(enabled_only=True))]
        down = bool(enabled) and all(s["consecutive_failures"] >= DOWN_AFTER_FAILS for s in enabled)
        if down and not _state["provider_down"]:
            push("TV Hub: leverantören svarar inte", "Ingen av portalerna svarar. Tittandet kan sluta fungera.")
        elif not down and _state["provider_down"]:
            push("TV Hub: leverantören svarar igen", "Portalen fungerar igen.")
        _state["provider_down"] = down
    if _state["last_event"] is None:
        row = db.q1("SELECT MAX(id) AS m FROM events")
        _state["last_event"] = row["m"] or 0
    rows = db.q("SELECT id, kind, detail FROM events WHERE id > ? AND kind = 'recording_failed' ORDER BY id",
                (_state["last_event"],))
    row = db.q1("SELECT MAX(id) AS m FROM events")
    _state["last_event"] = row["m"] or _state["last_event"]
    if cfg["recording"] and rows:
        push("TV Hub: inspelning misslyckades",
             "En inspelning misslyckades." if len(rows) == 1 else f"{len(rows)} inspelningar misslyckades.")
    hours = cfg["box_hours"]
    if hours:
        limit = hours * 3600
        for dev in db.q("SELECT id, name, last_seen FROM devices WHERE model != 'integration' AND last_seen IS NOT NULL"):
            try:
                stamp = datetime.fromisoformat(dev["last_seen"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                seen = stamp.timestamp()
            except ValueError:
                continue
            quiet = now - seen
            if quiet < limit:
                _state["boxes"].pop(dev["id"], None)
            elif now - _state["boxes"].get(dev["id"], 0) >= BOX_REPEAT_S and quiet < 30 * 86400:
                _state["boxes"][dev["id"]] = now
                push("TV Hub: tyst box", f"{dev['name']} har inte hörts av på {int(quiet // 3600)} timmar.")
    return sent


def loop(stop_event):
    while not stop_event.wait(60):
        try:
            check_once()
        except Exception as exc:  # noqa: BLE001
            print(f"[fel] avbrottsnotiser: {exc}", flush=True)
