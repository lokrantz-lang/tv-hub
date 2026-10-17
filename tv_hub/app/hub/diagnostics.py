"""Diagnostik: avgör om ett problem ligger hos källan, internet, hemnätet
eller kontot. Varje slutsats redovisar sina belägg."""
import json
import statistics
import time
from datetime import timedelta
import urllib.error
import urllib.request

from . import catalog, db, devices, portals
from .config import CFG
from .texts import explain
from .xtream import classify

MIN_MBPS = 30  # Toucans krav på hastighet

def _finding(level, area, title, detail="", evidence=None):
    return {"level": level, "area": area, "title": title, "detail": detail, "evidence": evidence or []}


def _internet_state():
    rows = db.q("SELECT ok, ms, ts FROM net_checks ORDER BY id DESC LIMIT 3")
    if not rows:
        return None, []
    down = len(rows) >= 2 and not rows[0]["ok"] and not rows[1]["ok"]
    return (not down), [dict(r) for r in rows]


def _account():
    row = db.q1(
        """SELECT c.*, p.name AS portal_name FROM checks c JOIN portals p ON p.id = c.portal_id
           WHERE c.api_ok = 1 ORDER BY c.id DESC LIMIT 1"""
    )
    return dict(row) if row else None


def overview():
    findings = []
    internet_ok, net_rows = _internet_state()
    items, best = portals.summaries()
    enabled = [p for p in items if p["enabled"]]

    # Internet
    if internet_ok is False:
        findings.append(_finding(
            "bad", "internet", "Din internetuppkoppling verkar vara nere",
            "Home Assistant når inte referensservrarna. Felet ligger hos dig eller din internetleverantör, inte hos Toucan.",
            [f"{r['ts']}: {'ok' if r['ok'] else 'fel'}" for r in net_rows],
        ))
    elif internet_ok:
        findings.append(_finding("ok", "internet", "Internet fungerar",
                                 f"Svarstid till referensserver {net_rows[0]['ms']} ms."))

    # Källan
    if not enabled:
        findings.append(_finding("warn", "source", "Inga portaler är inlagda",
                                 "Lägg till din Toucan-portal under Portaler."))
    else:
        failing = [p for p in enabled if p["ok"] is False and p["consecutive_failures"] >= 2]
        unknown = [p for p in enabled if p["ok"] is None]
        if len(failing) == len(enabled) and internet_ok is not False:
            reasons = sorted({explain(p["last_check"]["error_kind"]) for p in failing if p["last_check"]})
            findings.append(_finding(
                "bad", "source", "Toucan svarar inte",
                "Alla portaler misslyckas medan internet fungerar. Felet ligger hos källan: " + "; ".join(reasons) + ".",
                [f"{p['name']}: {p['consecutive_failures']} fel i rad" for p in failing],
            ))
        elif failing:
            active = next((p for p in items if p["active"]), None)
            findings.append(_finding(
                "warn", "source", f"{len(failing)} av {len(enabled)} portaler svarar inte",
                (f"Boxarna använder {active['name']}." if active else "") +
                " " + "; ".join(f"{p['name']}: {explain(p['last_check']['error_kind'])}" for p in failing if p["last_check"]),
            ))
        elif unknown and len(unknown) == len(enabled):
            findings.append(_finding("warn", "source", "Väntar på första kontrollen", "Det tar upp till två minuter."))
        else:
            active = next((p for p in items if p["active"]), None)
            if active:
                slow = (
                    active["api_ms_median_15m"] and active["api_ms_median_24h"]
                    and active["api_ms_median_15m"] > 1000
                    and active["api_ms_median_15m"] > 3 * active["api_ms_median_24h"]
                )
                if slow:
                    findings.append(_finding(
                        "warn", "source", "Toucan svarar ovanligt långsamt",
                        f"Svarstid senaste 15 min {active['api_ms_median_15m']} ms mot normalt "
                        f"{active['api_ms_median_24h']} ms. Källan är troligen hårt belastad.",
                    ))
                else:
                    findings.append(_finding(
                        "ok", "source", f"Toucan svarar ({active['name']})",
                        f"Svarstid {active['api_ms_median_15m'] or active['last_check']['api_ms']} ms.",
                    ))

    # Kontot
    account = _account()
    if account:
        if account["exp_date"]:
            days = (account["exp_date"] - time.time()) / 86400
            if days < 0:
                findings.append(_finding("bad", "account", "Abonnemanget har gått ut", ""))
            elif days < 7:
                findings.append(_finding("warn", "account", f"Abonnemanget går ut om {int(days)} dagar",
                                         time.strftime("%Y-%m-%d", time.localtime(account["exp_date"]))))
        ours = len(devices.active_sessions())
        if account["active_cons"] is not None and account["active_cons"] > ours:
            findings.append(_finding(
                "warn", "account", "Toucan ser en anslutning som inte kommer från TV Hub",
                f"Leverantören rapporterade {account['active_cons']} aktiv(a) anslutning(ar) av "
                f"{account['max_connections']} tillåtna, men inga eller färre boxar tittar via TV Hub. "
                "En annan app eller enhet strömmar troligen, till exempel MyTVOnline. "
                "Två samtidiga strömmar kan leda till att kontot stängs av.",
                [f"Kontroll {account['ts']}"],
            ))

    # Katalog
    runs = catalog.sync_runs(1)
    if runs:
        run = runs[0]
        if run["status"] in ("failed", "partial") and run["detail"]:
            bad_steps = [s for s in run["detail"].get("steps", []) if not s.get("ok") or s.get("kept_old")]
            label = {"live": "kanaler", "vod": "filmer", "series": "serier"}
            lines = [
                f"{label.get(s['type'], s['type'])} ({'kategorier' if s['part'] == 'categories' else 'lista'}): "
                f"{explain(s.get('error_kind'))} efter {s.get('ms', '?')} ms"
                for s in bad_steps
            ]
            if run["detail"].get("error"):
                lines.append(run["detail"]["error"])
            source_ok = any(p["ok"] for p in enabled)
            where = ("Inloggningen mot källan fungerar, så det är källans katalogtjänst som felar."
                     if source_ok else "Källan svarar inte alls just nu.")
            findings.append(_finding(
                "warn", "catalog", "Listan kunde inte uppdateras helt", where, lines,
            ))
        elif run["status"] == "ok":
            findings.append(_finding("ok", "catalog", "Listorna är uppdaterade", f"Senast {run['finished']}"))

    # Uppspelning
    findings.extend(playback_findings(minutes=30))

    order = {"bad": 0, "warn": 1, "ok": 2}
    findings.sort(key=lambda f: order[f["level"]])
    worst = findings[0]["level"] if findings else "ok"
    return {"level": worst, "findings": findings, "generated": db.iso()}


# ---------- uppspelningsproblem ----------
def _events(kind, minutes, device_id=None):
    sql = "SELECT * FROM events WHERE kind = ? AND ts >= ?"
    args = [kind, db.ago(minutes=minutes)]
    if device_id is not None:
        sql += " AND device_id = ?"
        args.append(device_id)
    out = []
    for row in db.q(sql + " ORDER BY id DESC", args):
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
        out.append(item)
    return out


def classify_stall(event):
    """Avgör var ett hack i bilden troligen kommer ifrån."""
    detail = event["detail"]
    ts = db.parse_iso(event["ts"])
    w0, w1 = db.iso(ts - timedelta(minutes=5)), db.iso(ts + timedelta(minutes=2))
    evidence = []

    # Internet vid tillfället
    net = db.q("SELECT ok, ts FROM net_checks WHERE ts BETWEEN ? AND ?", (w0, w1))
    if net and not any(r["ok"] for r in net):
        return "internet", "Internet var nere när det hackade.", evidence

    # Källans API vid tillfället
    session = db.q1("SELECT portal_id FROM sessions WHERE id = ?", (event["session_id"],)) if event["session_id"] else None
    if session and session["portal_id"]:
        checks = db.q(
            "SELECT api_ok, api_ms FROM checks WHERE portal_id = ? AND ts BETWEEN ? AND ?",
            (session["portal_id"], w0, w1),
        )
        if checks and any(not c["api_ok"] for c in checks):
            evidence.append("Källans API misslyckades runt samma tid.")
            return "source", "Källan hade problem samtidigt som det hackade.", evidence
        base = [c["api_ms"] for c in db.q(
            "SELECT api_ms FROM checks WHERE portal_id = ? AND api_ok = 1 AND ts >= ?",
            (session["portal_id"], db.ago(hours=24)),
        ) if c["api_ms"]]
        now_ms = [c["api_ms"] for c in checks if c["api_ms"]]
        if base and now_ms and statistics.median(now_ms) > 3 * statistics.median(base) and statistics.median(now_ms) > 1000:
            evidence.append(f"Källans svarstid {round(statistics.median(now_ms))} ms mot normalt {round(statistics.median(base))} ms.")
            return "source", "Källan var hårt belastad när det hackade.", evidence

    # Hemnätet (box till Home Assistant)
    lan = _events("speedtest", 60, event["device_id"])
    lan_mbps = lan[0]["detail"].get("mbps") if lan else None
    if lan_mbps is not None:
        evidence.append(f"Boxens hastighet i hemnätet: {lan_mbps} Mbit/s.")
        if lan_mbps < MIN_MBPS:
            return "lan", (f"Boxens anslutning i hemnätet klarar bara {lan_mbps} Mbit/s. "
                           "Wi-Fi eller kabeln till boxen är flaskhalsen. Prova kabel eller flytta routern närmare."), evidence

    bandwidth = detail.get("bandwidth_bps")
    bitrate = detail.get("bitrate_bps")
    if bandwidth:
        evidence.append(f"Uppmätt nedladdning i boxen: {round(bandwidth / 1e6, 1)} Mbit/s.")
    if bitrate:
        evidence.append(f"Strömmens bithastighet: {round(bitrate / 1e6, 1)} Mbit/s.")
    if bandwidth and bitrate and bandwidth < bitrate * 1.2:
        where = "Hemnätet är snabbt och källans API mådde bra" if lan_mbps and lan_mbps >= MIN_MBPS else "Källans API mådde bra"
        return "path", (f"{where}, men strömmen kom inte fram tillräckligt fort. Det pekar på källans strömserver "
                        "eller vägen via din internetleverantör. Kör kapacitetstestet i Diagnostik för att skilja dem åt."), evidence

    if lan_mbps is None:
        evidence.append("Inget hastighetstest från boxen ännu.")
    return "unknown", "För lite underlag för en säker slutsats. Mönstret över flera hack ger tydligare svar.", evidence


def playback_findings(minutes=30):
    stalls = _events("stall", minutes)
    errors = _events("error", minutes)
    failovers = _events("failover", minutes)
    names = {d["id"]: d["name"] for d in db.rows(db.q("SELECT id, name FROM devices"))}
    findings = []
    if stalls:
        votes = {}
        evidence = []
        for event in stalls[:20]:
            area, text, ev = classify_stall(event)
            votes.setdefault(area, []).append(text)
            evidence.append(f"{event['ts']} {names.get(event['device_id'], '?')}: {text}")
        area = max(votes, key=lambda a: len(votes[a]))
        titles = {
            "internet": "Bilden hackar: din internetuppkoppling",
            "source": "Bilden hackar: källan (Toucan)",
            "lan": "Bilden hackar: nätverket till boxen",
            "path": "Bilden hackar: källans strömserver eller din internetleverantör",
            "unknown": "Bilden har hackat",
        }
        findings.append(_finding(
            "warn", area if area != "unknown" else "playback",
            f"{titles[area]} ({len(stalls)} {'gång' if len(stalls) == 1 else 'gånger'} senaste {minutes} min)",
            votes[area][0], evidence[:8],
        ))
    for event in errors[:3]:
        findings.append(_finding(
            "warn", "playback", f"Uppspelningsfel på {names.get(event['device_id'], 'en box')}",
            str(event["detail"].get("message", "")), [event["ts"]],
        ))
    if failovers:
        findings.append(_finding(
            "warn", "source", f"Automatiskt portalbyte {len(failovers)} gånger",
            "Boxarna bytte portal efter fel. Se Händelser.", [f"{e['ts']}: {e['detail']}" for e in failovers[:5]],
        ))
    return findings


# ---------- kapacitetstest från servern ----------
def capacity_test(kind="vod", item_id=None, seconds=8):
    """Laddar ner från källan i några sekunder och mäter hastigheten.
    Körs bara när ingen box tittar, eftersom testet är en egen ström."""
    if devices.active_sessions():
        return {"ok": False, "error": "En box tittar just nu. Testet använder en egen ström och "
                                      "skulle ge två samtidiga anslutningar. Stoppa uppspelningen först."}
    account = _account()
    if account and account["active_cons"] and account["max_connections"] and \
            account["active_cons"] >= account["max_connections"]:
        return {"ok": False, "error": "Toucan rapporterar att alla tillåtna anslutningar redan används "
                                      "(troligen av en annan app). Testet körs inte."}
    portal = portals.best_portal()
    if portal is None:
        return {"ok": False, "error": "Ingen fungerande portal."}
    if kind not in ("vod", "live"):
        kind = "vod"
    if item_id is None:
        row = db.q1("SELECT item_id, ext, name FROM items WHERE type = ? ORDER BY added DESC LIMIT 1", (kind,))
        if not row:
            return {"ok": False, "error": "Katalogen är tom. Synka först."}
        item_id, ext, name = row["item_id"], row["ext"], row["name"]
    else:
        item = catalog.get_item(kind, item_id)
        if not item:
            return {"ok": False, "error": "Objektet finns inte i katalogen."}
        ext, name = item["ext"], item["name"]
    ext = portals.output_format(portal) if kind == "live" else (ext or "mp4")
    url = portals.client(portal).stream_url(kind, item_id, ext)

    seconds = max(3, min(int(seconds), 20))
    db.set_setting("server_test_until", time.time() + seconds + 5)
    result = {"kind": kind, "item": name, "portal": portal["name"], "seconds": seconds}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": CFG["user_agent"]})
        start = time.perf_counter()
        with urllib.request.urlopen(request, timeout=CFG["timeout"]) as response:
            first = response.read(64 * 1024)
            ttfb = time.perf_counter() - start
            total = len(first)
            last_chunk = time.perf_counter()
            max_gap = 0.0
            deadline = start + seconds
            while time.perf_counter() < deadline:
                chunk = response.read(256 * 1024)
                now_t = time.perf_counter()
                if not chunk:
                    break
                max_gap = max(max_gap, now_t - last_chunk)
                last_chunk = now_t
                total += len(chunk)
            elapsed = time.perf_counter() - start
        mbps = round(total * 8 / max(elapsed - ttfb, 0.001) / 1e6, 1)
        result.update(ok=True, mbps=mbps, ttfb_ms=round(ttfb * 1000), max_gap_ms=round(max_gap * 1000),
                      bytes=total)
        if kind == "vod":
            if mbps >= MIN_MBPS:
                result["verdict"] = (f"Källan levererar {mbps} Mbit/s till ditt hem, över Toucans krav på {MIN_MBPS}. "
                                     "Både källan och din internetleverantör räcker just nu. Hackar det i boxen, "
                                     "kör hastighetstestet på boxen för att kontrollera hemnätet.")
            else:
                result["verdict"] = (f"Bara {mbps} Mbit/s från källan, under Toucans krav på {MIN_MBPS}. "
                                     "Antingen är källan belastad eller så begränsar din internetleverantör. "
                                     "Jämför med en vanlig hastighetsmätning: är den hög är det källan.")
        else:
            result["verdict"] = (f"Liveström: {mbps} Mbit/s, längsta paus {round(max_gap, 1)} s. "
                                 + ("Pauser över 2 sekunder betyder att källan inte levererar jämnt."
                                    if max_gap > 2 else "Jämn leverans från källan."))
    except urllib.error.HTTPError as exc:
        result.update(ok=False, error=f"HTTP {exc.code}", verdict=f"Källan nekade strömmen: {explain(f'http_{exc.code}')}.")
    except (urllib.error.URLError, OSError) as exc:
        kind_err = classify(exc)
        result.update(ok=False, error=str(exc), verdict=f"Strömmen gick inte att hämta: {explain(kind_err)}.")
    finally:
        db.set_setting("server_test_until", 0)
    db.log_event("server_test", result)
    return result


def server_test_running():
    return (db.get_setting("server_test_until", 0) or 0) > time.time()


def recent_events(limit=100):
    names = {d["id"]: d["name"] for d in db.rows(db.q("SELECT id, name FROM devices"))}
    out = []
    for row in db.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)):
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
        item["device"] = names.get(item["device_id"])
        out.append(item)
    return out


def portal_history(portal_id, hours=24):
    out = db.rows(db.q(
        """SELECT ts, tcp_ok, connect_ms, api_ok, api_ms, error_kind, error, active_cons, max_connections
           FROM checks WHERE portal_id = ? AND ts >= ? ORDER BY id DESC LIMIT 1000""",
        (portal_id, db.ago(hours=hours)),
    ))
    for row in out:
        row["error_text"] = explain(row["error_kind"]) if row["error_kind"] else None
    return out


def cleanup():
    cutoff = db.ago(days=CFG["retention_days"])
    db.x("DELETE FROM checks WHERE ts < ?", (cutoff,))
    db.x("DELETE FROM net_checks WHERE ts < ?", (cutoff,))
    db.x("DELETE FROM events WHERE ts < ?", (cutoff,))
    db.x("DELETE FROM sessions WHERE ended IS NOT NULL AND ended < ?", (cutoff,))
    db.x("DELETE FROM sync_runs WHERE id NOT IN (SELECT id FROM sync_runs ORDER BY id DESC LIMIT 50)")
