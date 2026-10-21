"""AI-delen av Filmkväll. Valfri och utbytbar: allt annat fungerar utan den.

Tjänster:
  openai     OpenAI (api.openai.com)
  anthropic  Anthropic (Claude)
  custom     egen server som följer OpenAI:s standard, t.ex. Ollama hemma
             (http://192.168.x.x:11434/v1). Ingen nyckel krävs om servern inte vill ha någon.

Det som skickas: tittarlägets namn och smaktext, titlar ni gillat/inte gillat/sett och ett
urval kandidater (titel, år, genre, längd, betyg, kort handling). Inga namn på personer,
inga konton, inga adresser.

Förslagen räknas på natten (kl 04) och när du frågar i webbadminen. Ingen väntar på AI:n när
appen visar Filmkväll: finns inga färska AI-förslag används de lokala.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request

from . import catalog, db

PROVIDERS = ("off", "openai", "anthropic", "custom")
DEFAULT_URL = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "custom": "",
}
FRESH_HOURS = {"ai": 36, "chat": 24, "import": 24}
NIGHT_HOUR = 4
_run_lock = threading.Lock()


class AiError(Exception):
    pass


# ====================================================================
# Inställningar
# ====================================================================
def settings(masked=True):
    cfg = {
        "provider": db.get_setting("ai_provider") or "off",
        "url": db.get_setting("ai_url") or "",
        "model": db.get_setting("ai_model") or "",
        "nightly": db.get_setting("ai_nightly", True),
        "last_run": db.get_setting("ai_last_run"),
    }
    key = db.get_setting("ai_key") or ""
    if masked:
        cfg["has_key"] = bool(key)
        cfg["key_hint"] = ("…" + key[-4:]) if key else ""
    else:
        cfg["key"] = key
    cfg["enabled"] = cfg["provider"] != "off" and bool(cfg["model"]) and (bool(key) or cfg["provider"] == "custom")
    return cfg


def save_settings(data):
    if "provider" in data:
        provider = data.get("provider") or "off"
        if provider not in PROVIDERS:
            raise ValueError("Okänd AI-tjänst.")
        db.set_setting("ai_provider", provider)
    if "url" in data:
        url = str(data.get("url") or "").strip().rstrip("/")
        if url and not re.match(r"^https?://", url):
            raise ValueError("Adressen ska börja med http:// eller https://")
        db.set_setting("ai_url", url)
    if "model" in data:
        db.set_setting("ai_model", str(data.get("model") or "").strip()[:100])
    if data.get("key") is not None and "key" in data:
        key = str(data.get("key")).strip()
        if len(key) > 500 or any(c.isspace() for c in key):
            raise ValueError("Nyckeln ser inte rätt ut.")
        db.set_setting("ai_key", key)
    if "nightly" in data:
        db.set_setting("ai_nightly", bool(data.get("nightly")))
    return settings()


# ====================================================================
# Anrop
# ====================================================================
def _post(url, headers, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers=dict(headers, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        try:
            parsed = json.loads(detail)
            err = parsed.get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or detail
        except ValueError:
            pass
        hint = {401: " (fel nyckel?)", 403: " (nyckeln saknar behörighet?)", 404: " (fel adress eller modell?)",
                429: " (för många anrop eller slut på kredit)"}.get(exc.code, "")
        raise AiError(f"HTTP {exc.code}{hint}: {detail}")
    except (urllib.error.URLError, OSError) as exc:
        raise AiError(f"Når inte AI-tjänsten: {getattr(exc, 'reason', exc)}")
    except ValueError:
        raise AiError("AI-tjänsten svarade inte med JSON.")


def chat(system, user, max_tokens=1800):
    cfg = settings(masked=False)
    if not cfg["enabled"]:
        raise AiError("Ingen AI-tjänst är vald. Välj under Filmkväll → AI.")
    provider = cfg["provider"]
    base = (cfg["url"] or DEFAULT_URL[provider]).rstrip("/")
    if not base:
        raise AiError("Ange adressen till din server, t.ex. http://192.168.1.20:11434/v1")
    if provider == "anthropic":
        data = _post(f"{base}/messages",
                     {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"},
                     {"model": cfg["model"], "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]}, timeout=120)
        parts = [b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text"]
        text = "".join(parts)
    else:
        headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
        data = _post(f"{base}/chat/completions", headers,
                     {"model": cfg["model"], "temperature": 0.4, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                     timeout=240 if provider == "custom" else 120)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise AiError("Oväntat svar från AI-tjänsten.")
    if not text.strip():
        raise AiError("AI-tjänsten gav ett tomt svar.")
    return text


def test():
    started = time.time()
    try:
        reply = chat("Du svarar kort.", "Svara bara med ordet OK.", max_tokens=20)
        return {"ok": True, "reply": reply.strip()[:80], "ms": int((time.time() - started) * 1000)}
    except AiError as exc:
        return {"ok": False, "error": str(exc)}


# ====================================================================
# Underlaget
# ====================================================================
SYSTEM = (
    "Du är filmrådgivare för en familj i Sverige som tittar via en IPTV-tjänst. "
    "Du får ett tittarläge (vem som tittar), deras smak och en lista med kandidater som finns att se. "
    "Välj BARA bland kandidaterna och använd deras id exakt. Tänk på variation, på det de gillat och "
    "inte gillat, och på regler som barnvänligt och längd. När barn tittar: välj bara det som passar barn; "
    "är du osäker, välj bort titeln. Skriv motiveringar på svenska, högst 12 ord, utan spoilers."
)


def _line(work):
    parts = [str(work["item_id"]), f"{work['name']}" + (f" ({work['year']})" if work.get("year") else "")]
    if work.get("genre"):
        parts.append(str(work["genre"])[:60])
    if work.get("duration_min"):
        parts.append(f"{work['duration_min']} min")
    if work.get("rating_num"):
        parts.append(f"betyg {work['rating_num']:.1f}")
    if work.get("plot"):
        parts.append(re.sub(r"\s+", " ", str(work["plot"]))[:160])
    return " | ".join(parts)


def build_prompt(viewer_id, kind="vod", question=None, count=15, candidates=None):
    from . import taste
    v = taste.viewer(viewer_id)
    if v is None:
        raise LookupError("Okänt tittarläge.")
    kind = kind if kind in taste.KINDS else "vod"
    cands = candidates if candidates is not None else taste.candidates_for_ai(v["id"], kind, 80)
    rules = v["rules"]
    liked = db.q("SELECT title, value FROM ratings WHERE viewer_id = ? AND type = ? AND value > 0 "
                 "ORDER BY value DESC, at DESC LIMIT 30", (v["id"], kind))
    disliked = db.q("SELECT title FROM ratings WHERE viewer_id = ? AND type = ? AND value < 0 ORDER BY at DESC LIMIT 15",
                    (v["id"], kind))
    seen = db.q("SELECT DISTINCT title FROM seen_log WHERE viewer_id = ? AND type = ? ORDER BY at DESC LIMIT 25",
                (v["id"], kind))
    wishes = db.q("SELECT title FROM wishlist WHERE type = ? AND (viewer_id IS NULL OR viewer_id = ?) LIMIT 20",
                  (kind, v["id"]))
    what = "filmer" if kind == "vod" else "serier"
    lines = [f"Tittarläge: {v['name']}"]
    if v.get("taste"):
        lines.append(f"Det här uppskattar vi: {v['taste']}")
    rule_text = []
    if rules.get("kids"):
        rule_text.append("barn tittar: bara barnvänligt")
    if rules.get("max_minutes"):
        rule_text.append(f"högst {rules['max_minutes']} minuter")
    if rules.get("exclude_genres"):
        rule_text.append("inte: " + ", ".join(rules["exclude_genres"]))
    if rule_text:
        lines.append("Regler: " + "; ".join(rule_text))
    if liked:
        lines.append("Gillade: " + "; ".join(f"{r['title']}{' (älskade)' if r['value'] == 2 else ''}" for r in liked))
    if disliked:
        lines.append("Gillade inte: " + "; ".join(r["title"] for r in disliked))
    if seen:
        lines.append("Sett nyligen: " + "; ".join(r["title"] for r in seen))
    if wishes:
        lines.append("Vill se: " + "; ".join(r["title"] for r in wishes))
    lines.append("")
    lines.append(f"Kandidater ({what}), en per rad: id | titel | genre | längd | betyg | handling")
    lines += [_line(w) for w in cands]
    lines.append("")
    if question:
        lines.append(f"Fråga: {question}")
        lines.append(f"Svara först kort på svenska. Avsluta med JSON på en egen rad: "
                     f'{{"picks": [{{"id": "...", "reason": "..."}}]}} med högst {count} {what} ur kandidaterna.')
    else:
        lines.append(f"Välj de {count} bästa {what} för kvällen i den ordning du rekommenderar dem. "
                     f'Svara BARA med JSON: {{"picks": [{{"id": "...", "reason": "..."}}]}}')
    return SYSTEM, "\n".join(lines), cands


def parse_picks(text, candidates):
    """Läser {"picks": [...]} ur svaret. Klarar text runt omkring, kodblock och titlar i stället för id."""
    by_id = {str(w["item_id"]): w for w in candidates}
    by_title = {}
    from . import taste
    for w in candidates:
        by_title.setdefault(taste.clean_title(w["name"]), w)
    decoder = json.JSONDecoder()
    found = None
    for match in re.finditer(r"[\[{]", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("picks"), list):
            found = obj["picks"]
            break
        if isinstance(obj, list) and obj and all(isinstance(x, (dict, str, int)) for x in obj):
            found = obj
            break
    picks, seen = [], set()
    for entry in found or []:
        if isinstance(entry, dict):
            ident = str(entry.get("id") or "").strip()
            reason = str(entry.get("reason") or "").strip()[:120]
            title = str(entry.get("title") or entry.get("name") or "")
        else:
            ident, reason, title = str(entry).strip(), "", str(entry)
        work = by_id.get(ident) or by_title.get(taste.clean_title(title or ident))
        if work and work["item_id"] not in seen:
            seen.add(work["item_id"])
            picks.append({"id": work["item_id"], "key": work["key"], "reason": reason})
    if not found:
        # Inget JSON: leta efter id:n i texten ("123 | Titel").
        for ident in re.findall(r"\b(\d{2,9})\b", text):
            work = by_id.get(ident)
            if work and work["item_id"] not in seen:
                seen.add(work["item_id"])
                picks.append({"id": work["item_id"], "key": work["key"], "reason": ""})
    return picks


def store(viewer_id, kind, picks, source):
    db.x("INSERT OR REPLACE INTO ai_picks (viewer_id, type, created, source, picks) VALUES (?,?,?,?,?)",
         (viewer_id, kind, int(time.time()), source, json.dumps(picks, ensure_ascii=False)))


def clear(viewer_id, kind):
    db.x("DELETE FROM ai_picks WHERE viewer_id = ? AND type = ?", (viewer_id, kind))


def current_picks(viewer_id, kind):
    row = db.q1("SELECT * FROM ai_picks WHERE viewer_id = ? AND type = ?", (viewer_id, kind))
    if not row:
        return []
    if time.time() - row["created"] > FRESH_HOURS.get(row["source"], 24) * 3600:
        return []
    try:
        return json.loads(row["picks"])
    except ValueError:
        return []


def picks_info(viewer_id, kind):
    row = db.q1("SELECT created, source, picks FROM ai_picks WHERE viewer_id = ? AND type = ?", (viewer_id, kind))
    if not row:
        return None
    return {"created": row["created"], "source": row["source"], "count": len(json.loads(row["picks"] or "[]")),
            "fresh": bool(current_picks(viewer_id, kind))}


# ====================================================================
# Användning
# ====================================================================
def rerank(viewer_id, kind):
    system, user, cands = build_prompt(viewer_id, kind)
    if not cands:
        return 0
    picks = parse_picks(chat(system, user), cands)
    if picks:
        store(viewer_id, kind, picks, "ai")
    return len(picks)


def run_all(trigger="manual"):
    from . import taste
    if not _run_lock.acquire(blocking=False):
        raise AiError("AI-förslagen räknas redan.")
    try:
        results, errors = [], []
        for v in taste.viewers():
            for kind in taste.KINDS:
                try:
                    results.append({"viewer": v["name"], "kind": kind, "picks": rerank(v["id"], kind)})
                except (AiError, LookupError) as exc:
                    errors.append(f"{v['name']}/{kind}: {exc}")
        last = {"at": db.iso(), "trigger": trigger, "ok": not errors, "results": results, "errors": errors[:5]}
        db.set_setting("ai_last_run", last)
        db.log_event("ai_run", {"ok": not errors, "errors": len(errors)})
        return last
    finally:
        _run_lock.release()


def ask(viewer_id, kind, question):
    question = str(question or "").strip()[:500]
    if not question:
        raise ValueError("Skriv en fråga.")
    system, user, cands = build_prompt(viewer_id, kind, question=question, count=10)
    text = chat(system, user)
    picks = parse_picks(text, cands)
    answer = text
    cut = re.search(r"[\[{]\s*\"?picks|```", text)
    if cut and cut.start() > 0:
        answer = text[:cut.start()].strip()
    items = []
    for p in picks:
        item = catalog.get_item(kind, p["id"])
        if item:
            item["reason"] = p.get("reason")
            items.append(item)
    return {"answer": answer.strip(), "picks": picks, "items": items}


def export_text(viewer_id, kind):
    """Allt i en text att klistra in i valfri AI-chatt."""
    system, user, _ = build_prompt(viewer_id, kind)
    return f"{system}\n\n{user}"


def paste(viewer_id, kind, text):
    """Svaret från valfri AI-chatt blir Filmkvällens lista i ett dygn."""
    from . import taste
    cands = taste.candidates_for_ai(viewer_id, kind, 80)
    picks = parse_picks(str(text or ""), cands)
    if not picks:
        # Svaret kan gälla titlar utanför urvalet: matcha mot hela katalogen.
        picks = parse_picks(str(text or ""), taste.catalog_cache(kind)["items"])
    if not picks:
        raise ValueError("Hittade inga titlar i svaret. Klistra in hela svaret, med JSON-delen.")
    store(viewer_id, kind, picks, "import")
    return {"count": len(picks)}


def loop(stop_event):
    """Kl 04 varje natt, om en AI-tjänst är vald och nattkörning påslagen."""
    while not stop_event.wait(600):
        try:
            cfg = settings()
            if not (cfg["enabled"] and cfg["nightly"]):
                continue
            now = time.localtime()
            if now.tm_hour != NIGHT_HOUR:
                continue
            last = (cfg.get("last_run") or {}).get("at") or ""
            if last[:10] == time.strftime("%Y-%m-%d", now) and (cfg.get("last_run") or {}).get("trigger") == "night":
                continue
            run_all("night")
        except Exception as exc:
            print(f"[fel] AI-förslag: {exc!r}", flush=True)
