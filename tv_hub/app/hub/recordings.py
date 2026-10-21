"""Inspelningar.

Själva filen spelas in av boxen och sparas på dess USB-minne. TV Hub håller
schemat, så att man kan planera från webbadminen eller mobilen, och visar status.

Källa:
  live     programmet har inte sänts klart: boxen spelar in direktsändningen
  archive  programmet har redan sänts: boxen laddar ner det från Toucans arkiv (catch-up)
  vod      en film laddas ner till USB-minnet
  episode  ett avsnitt laddas ner till USB-minnet
"""
import re
import time

from . import catalog, db

MAX_LENGTH_S = 6 * 3600
MAX_SCHEDULED_PER_DEVICE = 200
ACTIVE_STATUSES = ("scheduled", "recording")
STATUSES = ("scheduled", "recording", "done", "failed", "deleted")
FIELDS = ("id, device_id, stream_id, channel, title, start, end, source, adult, status, "
          "session_id, size_bytes, file, error, created, updated, ext, series_id")
MEDIA_SOURCES = ("vod", "episode")


def _row(row):
    item = dict(row)
    item["adult"] = bool(item["adult"])
    return item


def create(device, scope, stream_id, title, start, end):
    stream_id = str(stream_id or "")
    if not catalog.in_scope("live", stream_id, scope):
        raise LookupError("Kanalen finns inte.")
    channel = catalog.get_item("live", stream_id)
    if channel is None:
        raise LookupError("Kanalen finns inte.")
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        raise ValueError("Starttid och sluttid krävs.")
    if end <= start:
        raise ValueError("Sluttiden måste vara efter starttiden.")
    if end - start > MAX_LENGTH_S:
        raise ValueError("En inspelning kan vara högst 6 timmar.")
    now = time.time()
    if end <= now:
        archive_days = int(channel.get("archive_days") or 0)
        if archive_days <= 0:
            raise ValueError("Programmet har redan sänts och kanalen har inget arkiv att spela in från.")
        if start < now - archive_days * 86400:
            raise ValueError(f"Programmet är äldre än kanalens arkiv ({archive_days} dagar).")
        source = "archive"
    else:
        source = "live"
    if end > now + 14 * 86400:
        raise ValueError("Det går att planera högst två veckor framåt.")

    existing = db.q1(
        "SELECT * FROM recordings WHERE device_id = ? AND stream_id = ? AND start = ? AND status != 'deleted'",
        (device["id"], stream_id, start),
    )
    if existing:
        return _row(existing), False
    count = db.q1("SELECT COUNT(*) AS n FROM recordings WHERE device_id = ? AND status = 'scheduled'",
                  (device["id"],))["n"]
    if count >= MAX_SCHEDULED_PER_DEVICE:
        raise ValueError("För många planerade inspelningar.")

    title = (str(title or "").strip() or channel["name"])[:200]
    adult = 1 if (scope.adult or channel.get("adult")) else 0
    rec_id = db.x(
        """INSERT INTO recordings (device_id, stream_id, channel, title, start, end, source, adult,
           status, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)""",
        (device["id"], stream_id, channel["name"], title, start, end, source, adult, db.iso(), db.iso()),
    ).lastrowid
    # Händelseloggen (Diagnostik) visar aldrig titlar på vuxeninspelningar.
    db.log_event("recording_created", {"title": "18+" if adult else title, "channel": "" if adult else channel["name"],
                                       "source": source},
                 device_id=device["id"])
    return get(rec_id), True


def create_media(device, scope, kind, item_id, title=None, ext=None, series_id=None, series_name=None):
    """Spela in (ladda ner) en film eller ett avsnitt till boxens USB-minne."""
    item_id = str(item_id or "")
    if kind not in MEDIA_SOURCES or not item_id or len(item_id) > 40:
        raise ValueError("Okänd film eller avsnitt.")
    if kind == "vod":
        if not catalog.in_scope("vod", item_id, scope):
            raise LookupError("Filmen finns inte.")
        item = catalog.get_item("vod", item_id) or {}
        title = (str(title or "").strip() or item.get("name") or f"Film {item_id}")[:200]
        ext = item.get("ext") or ext or "mp4"
        channel = "Film"
        adult = 1 if item.get("adult") else 0
    else:
        series_id = str(series_id or "")
        if not series_id or not catalog.in_scope("series", series_id, scope):
            raise LookupError("Serien finns inte.")
        series = catalog.get_item("series", series_id) or {}
        channel = (series_name or series.get("name") or "Serie")[:120]
        title = (str(title or "").strip() or f"{channel} avsnitt {item_id}")[:200]
        adult = 1 if series.get("adult") else 0
    ext = str(ext or "mp4")
    if not ext.isalnum() or len(ext) > 8:
        ext = "mp4"
    existing = db.q1(
        "SELECT * FROM recordings WHERE device_id = ? AND stream_id = ? AND source = ? AND status != 'deleted'",
        (device["id"], item_id, kind),
    )
    if existing:
        return _row(existing), False
    count = db.q1("SELECT COUNT(*) AS n FROM recordings WHERE device_id = ? AND status = 'scheduled'",
                  (device["id"],))["n"]
    if count >= MAX_SCHEDULED_PER_DEVICE:
        raise ValueError("För många planerade inspelningar.")
    now = int(time.time())
    rec_id = db.x(
        """INSERT INTO recordings (device_id, stream_id, channel, title, start, end, source, adult,
           status, created, updated, ext, series_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?)""",
        (device["id"], item_id, channel, title, now, now, kind, adult, db.iso(), db.iso(), ext,
         series_id if kind == "episode" else None),
    ).lastrowid
    db.log_event("recording_created", {"title": "18+" if adult else title, "source": kind}, device_id=device["id"])
    return get(rec_id), True


def create_series(device, scope, series_id, detail, season=None):
    """Alla avsnitt i en serie (eller en säsong). detail = seriens avsnittslista från källan."""
    created = existing = 0
    name = detail.get("name") or (catalog.get_item("series", series_id) or {}).get("name") or "Serie"
    for s in detail.get("seasons") or []:
        if season not in (None, "") and str(s.get("season")) != str(season):
            continue
        for ep in s.get("episodes") or []:
            ep_title = str(ep.get("title") or "").strip()
            if ep_title and name.lower() in ep_title.lower():
                title = ep_title  # källan skriver ofta redan "Serie - S01E02 - Namn"
            else:
                title = f"{name} S{s.get('season')}E{ep.get('num')}"
                if ep_title and not re.match(r"^S\d+\s*E\d+$", ep_title, re.IGNORECASE):
                    title += f" · {ep_title}"
            _, new = create_media(device, scope, "episode", ep.get("id"), title, ep.get("ext"), series_id, name)
            if new:
                created += 1
            else:
                existing += 1
    if created + existing == 0:
        raise LookupError("Serien har inga avsnitt.")
    return {"created": created, "existing": existing}


def get(rec_id):
    row = db.q1(f"SELECT {FIELDS} FROM recordings WHERE id = ?", (rec_id,))
    return _row(row) if row else None


def for_device(device_id, adult=None):
    """adult=None ger alla (används av boxens schemaläggare). Annars bara det läget."""
    sql = f"SELECT {FIELDS} FROM recordings WHERE device_id = ?"
    args = [device_id]
    if adult is not None:
        sql += " AND adult = ? AND status != 'deleted'"
        args.append(1 if adult else 0)
    sql += " ORDER BY start DESC"
    return [_row(r) for r in db.q(sql, args)]


def all_recordings():
    rows = db.q(f"""SELECT r.{FIELDS.replace(', ', ', r.')}, d.name AS device_name
                    FROM recordings r LEFT JOIN devices d ON d.id = r.device_id
                    WHERE r.status != 'deleted' ORDER BY r.start DESC LIMIT 500""")
    return [_row(r) for r in rows]


def report(device, rec_id, status, size_bytes=None, file=None, error=None):
    row = db.q1("SELECT * FROM recordings WHERE id = ? AND device_id = ?", (rec_id, device["id"]))
    if not row:
        raise LookupError("Inspelningen finns inte.")
    if status not in ("recording", "done", "failed"):
        raise ValueError("Okänd status.")
    if row["status"] == "deleted":
        return _row(row)
    try:
        size_bytes = int(size_bytes) if size_bytes is not None else row["size_bytes"]
    except (TypeError, ValueError):
        size_bytes = row["size_bytes"]
    db.x("""UPDATE recordings SET status = ?, size_bytes = ?, file = ?, error = ?, updated = ?
            WHERE id = ?""",
         (status, size_bytes, str(file)[:500] if file else row["file"],
          str(error)[:300] if error else None, db.iso(), rec_id))
    if status in ("done", "failed"):
        db.log_event("recording_" + status, {"title": "18+" if row["adult"] else row["title"], "size": size_bytes,
                                             "error": error},
                     device_id=device["id"])
    return get(rec_id)


def remove_by_device(device, rec_id):
    """Boxen har redan stoppat jobbet och raderat filen."""
    return db.x("DELETE FROM recordings WHERE id = ? AND device_id = ?", (rec_id, device["id"])).rowcount > 0


def _stop_session(row):
    if row["session_id"]:
        db.x("UPDATE sessions SET stop_requested = ? WHERE id = ? AND ended IS NULL",
             (int(time.time()), row["session_id"]))


def stop(rec_id):
    """Från webbadminen: avsluta en pågående inspelning. Det som spelats in sparas."""
    row = db.q1("SELECT * FROM recordings WHERE id = ?", (rec_id,))
    if not row:
        raise LookupError("Inspelningen finns inte.")
    if row["status"] != "recording":
        raise ValueError("Inspelningen pågår inte.")
    _stop_session(row)
    return True


def remove(rec_id):
    """Från webbadminen. En planerad inspelning tas bort direkt. En pågående stoppas och
    en färdig markeras för radering; boxen raderar filen nästa gång den hämtar schemat."""
    row = db.q1("SELECT * FROM recordings WHERE id = ?", (rec_id,))
    if not row:
        raise LookupError("Inspelningen finns inte.")
    if row["status"] == "scheduled":
        db.x("DELETE FROM recordings WHERE id = ?", (rec_id,))
        return "removed"
    _stop_session(row)
    db.x("UPDATE recordings SET status = 'deleted', updated = ? WHERE id = ?", (db.iso(), rec_id))
    return "marked"


def attach_session(device, rec_id, stream_id, session_id):
    row = db.q1("SELECT * FROM recordings WHERE id = ? AND device_id = ?", (rec_id, device["id"]))
    if not row or str(row["stream_id"]) != str(stream_id):
        return False
    db.x("UPDATE recordings SET session_id = ?, status = CASE WHEN status = 'scheduled' THEN 'recording' "
         "ELSE status END, updated = ? WHERE id = ?", (session_id, db.iso(), rec_id))
    return True


def lookup_for_play(device, rec_id):
    try:
        rec_id = int(rec_id)
    except (TypeError, ValueError):
        return None
    row = db.q1("SELECT * FROM recordings WHERE id = ? AND device_id = ?", (rec_id, device["id"]))
    if not row or row["status"] == "deleted":
        return None
    return dict(row)


def expire():
    """Planerade direktinspelningar som aldrig startade (boxen var avstängd)."""
    cutoff = int(time.time()) - 600
    for row in db.q("SELECT * FROM recordings WHERE status = 'scheduled' AND source = 'live' AND end < ?",
                    (cutoff,)):
        db.x("UPDATE recordings SET status = 'failed', error = ?, updated = ? WHERE id = ?",
             ("Boxen var avstängd eller nåddes inte när inspelningen skulle börja.", db.iso(), row["id"]))
    # Pågående inspelningar där boxen tystnat sedan länge.
    for row in db.q("SELECT * FROM recordings WHERE status = 'recording' AND end < ? AND updated < ?",
                    (cutoff - 3600, db.ago(hours=2))):
        status = "done" if (row["size_bytes"] or 0) > 0 else "failed"
        db.x("UPDATE recordings SET status = ?, error = ?, updated = ? WHERE id = ?",
             (status, None if status == "done" else "Inspelningen avbröts.", db.iso(), row["id"]))
