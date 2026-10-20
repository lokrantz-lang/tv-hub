"""Appen utanför hemmet, via Nabu Casa.

Vägen är densamma som Home Assistants egen webbsida använder för tillägg (Ingress):

  telefon → https://<id>.ui.nabu.casa/api/hassio_ingress/<nyckel>/device/...  → tillägget (port 8080)

1. Telefonen loggar in i Home Assistant med en åtkomstnyckel för en vanlig (icke-admin)
   användare och ber om en ingress-session (websocket: supervisor/api /ingress/session).
2. Sessionen skickas som kakan ingress_session. Supervisor släpper bara igenom anrop med
   giltig session och lägger till X-Remote-User-Id/-Name.
3. Tillägget kräver dessutom boxens egen nyckel (X-TVHub-Token), precis som på port 8099.

För att vanliga användare ska få en session krävs panel_admin: false. Därför spärrar
tillägget själv webbadminen för alla som inte är administratörer i Home Assistant.

Själva videon går direkt från Toucan till telefonen och påverkas inte av detta.
"""
import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request

from . import db

SUPERVISOR_WS = os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor")
DEFAULT_APP_USER = "tvhub"
USERS_TTL = 300

_lock = threading.Lock()
_users = {"at": 0.0, "by_id": None, "error": None}
_ingress = {"entry": None, "at": 0.0}
_last_remote = {}


def supervisor_token():
    """Nyckeln till Supervisor. Basavbildningen (s6-overlay) lägger den i en fil i stället för i
    miljön när programmet inte startas via with-contenv, så båda ställena läses."""
    for name in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()
        for folder in ("/run/s6/container_environment", "/var/run/s6/container_environment"):
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    value = fh.read().strip()
                if value:
                    return value
            except OSError:
                pass
    return None


class WsError(Exception):
    pass


# ====================================================================
# Minimal websocket-klient (RFC 6455), bara det Home Assistant behöver
# ====================================================================
class WebSocket:
    def __init__(self, url, timeout=10, context=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("ws", "wss"):
            raise WsError("Adressen måste börja med ws:// eller wss://")
        secure = parts.scheme == "wss"
        host = parts.hostname
        port = parts.port or (443 if secure else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = context or ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        self.sock = sock
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        host_header = host if parts.port is None else f"{host}:{port}"
        request = (f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"User-Agent: TVHub\r\n\r\n")
        sock.sendall(request.encode())
        head = self._read_until(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in status_line + " ":
            raise WsError(f"Home Assistant svarade: {status_line.strip() or 'inget svar'}")
        import hashlib
        expect = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode())
                                  .digest()).decode()
        if expect.encode() not in head:
            raise WsError("Ogiltigt websocket-svar")

    def _read_until(self, marker):
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WsError("Anslutningen stängdes")
            self._buf += chunk
            if len(self._buf) > 65536:
                raise WsError("För stort svar")
        head, self._buf = self._buf.split(marker, 1)
        return head

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WsError("Anslutningen stängdes")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def send_text(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _send_control(self, opcode, payload=b""):
        mask = os.urandom(4)
        frame = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + bytes(
            b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(frame)

    def recv_text(self):
        message = b""
        while True:
            b0, b1 = self._read_exact(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read_exact(8))[0]
            if n > 16 * 1024 * 1024:
                raise WsError("För stort meddelande")
            mask = self._read_exact(4) if b1 & 0x80 else None
            data = self._read_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise WsError("Home Assistant stängde anslutningen")
            if opcode == 0x9:
                self._send_control(0xA, data)
                continue
            if opcode == 0xA:
                continue
            message += data
            if fin:
                return message.decode("utf-8")

    def recv_json(self):
        return json.loads(self.recv_text())

    def close(self):
        try:
            self._send_control(0x8, struct.pack(">H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def ha_session(url, token, commands, timeout=10, context=None):
    """Loggar in på Home Assistants websocket och kör [commands] i tur och ordning.
    Returnerar resultaten. Ett misslyckat kommando ger WsError."""
    ws = WebSocket(url, timeout=timeout, context=context)
    try:
        first = ws.recv_json()
        if first.get("type") != "auth_required":
            raise WsError("Oväntat svar från Home Assistant")
        ws.send_text(json.dumps({"type": "auth", "access_token": token}))
        auth = ws.recv_json()
        if auth.get("type") != "auth_ok":
            raise WsError("Åtkomstnyckeln godtogs inte av Home Assistant.")
        results = []
        for i, command in enumerate(commands, start=1):
            ws.send_text(json.dumps(dict(command, id=i)))
            while True:
                msg = ws.recv_json()
                if msg.get("id") == i and msg.get("type") == "result":
                    break
            if not msg.get("success"):
                err = msg.get("error") or {}
                raise WsError(err.get("message") or err.get("code") or "Kommandot misslyckades")
            results.append(msg.get("result"))
        return results
    finally:
        ws.close()


# ====================================================================
# Vem får använda webbadminen
# ====================================================================
def _load_users():
    token = supervisor_token()
    if not token:
        raise WsError("Ingen Supervisor-nyckel (körs utanför Home Assistant)")
    users = ha_session(SUPERVISOR_WS, token, [{"type": "config/auth/list"}])[0] or []
    by_id = {}
    for user in users:
        groups = user.get("group_ids") or []
        by_id[user.get("id")] = {
            "admin": bool(user.get("is_owner")) or "system-admin" in groups,
            "username": user.get("username") or "",
            "name": user.get("name") or "",
            "active": user.get("is_active", True),
        }
    return by_id


def user_info(user_id, refresh=False):
    """{"admin", "username", "name"} eller None om okänd. Kastar WsError om listan inte kan hämtas."""
    with _lock:
        fresh = _users["by_id"] is not None and time.time() - _users["at"] < USERS_TTL
        if fresh and not refresh and user_id in _users["by_id"]:
            return _users["by_id"][user_id]
    try:
        by_id = _load_users()
    except Exception as exc:
        with _lock:
            _users["error"] = str(exc)
        raise WsError(str(exc))
    with _lock:
        _users.update(by_id=by_id, at=time.time(), error=None)
    return by_id.get(user_id)


def app_user():
    return (db.get_setting("remote_user") or DEFAULT_APP_USER).strip()


def admin_allowed(headers):
    """Webbadminen: bara administratörer i Home Assistant.

    Saknas användaruppgifter (äldre Supervisor) släpps anropet igenom som tidigare.
    Går användarlistan inte att hämta nekas bara appens egen användare, så att du
    aldrig låses ute från din egen webbadmin."""
    user_id = (headers.get("X-Remote-User-Id") or "").strip()
    if not user_id:
        return True
    username = (headers.get("X-Remote-User-Name") or "").strip().lower()
    display = (headers.get("X-Remote-User-Display-Name") or "").strip().lower()
    blocked_name = app_user().lower()
    if blocked_name and blocked_name in (username, display):
        return False
    try:
        info = user_info(user_id)
    except WsError:
        return True
    if info is None:
        try:
            info = user_info(user_id, refresh=True)
        except WsError:
            return True
    return bool(info and info["admin"])


# ====================================================================
# Inställningar och uppgifter till appen
# ====================================================================
def ingress_entry():
    """/api/hassio_ingress/<nyckel> för det här tillägget (fast så länge tillägget är installerat)."""
    override = os.environ.get("INGRESS_ENTRY")
    if override:
        return override.rstrip("/")
    with _lock:
        if _ingress["entry"] and time.time() - _ingress["at"] < 3600:
            return _ingress["entry"]
    token = supervisor_token()
    if not token:
        return None
    try:
        request = urllib.request.Request(f"{SUPERVISOR_API}/addons/self/info",
                                         headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read().decode()).get("data") or {}
        entry = (data.get("ingress_entry") or "").rstrip("/") or None
    except Exception as exc:
        print(f"[fel] ingress-adress: {exc}", flush=True)
        return None
    with _lock:
        _ingress.update(entry=entry, at=time.time())
    return entry


def normalize_url(raw):
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("Adressen ska börja med https://, t.ex. https://abc123.ui.nabu.casa")
    host = parts.hostname
    return f"https://{host}" + (f":{parts.port}" if parts.port else "")


def settings():
    token = db.get_setting("remote_token") or ""
    return {
        "url": db.get_setting("remote_url") or "",
        "has_token": bool(token),
        "token_hint": ("…" + token[-4:]) if token else "",
        "app_user": app_user(),
        "ingress_entry": ingress_entry(),
        "last": db.get_setting("remote_last"),
        "last_test": db.get_setting("remote_test"),
        "users_error": _users.get("error"),
        "ready": bool(db.get_setting("remote_url") and token),
    }


def save_settings(data):
    if "url" in data:
        db.set_setting("remote_url", normalize_url(data.get("url")))
    if "token" in data and data.get("token") is not None:
        token = str(data.get("token")).strip()
        if token and (len(token) < 20 or len(token) > 1000 or any(c.isspace() for c in token)):
            raise ValueError("Åtkomstnyckeln ser inte rätt ut. Kopiera hela nyckeln från Home Assistant.")
        db.set_setting("remote_token", token)
    if "app_user" in data:
        name = str(data.get("app_user") or "").strip()
        if len(name) > 60:
            raise ValueError("För långt användarnamn.")
        db.set_setting("remote_user", name or DEFAULT_APP_USER)
    return settings()


def device_config():
    """Det appen behöver för att nå tillägget via Nabu Casa. Hämtas bara hemma (port 8099)."""
    url = db.get_setting("remote_url") or ""
    token = db.get_setting("remote_token") or ""
    entry = ingress_entry()
    if not (url and token and entry):
        missing = []
        if not url:
            missing.append("Nabu Casa-adress")
        if not token:
            missing.append("åtkomstnyckel")
        if not entry:
            missing.append("ingress-adress (tillägget körs inte i Home Assistant?)")
        return {"available": False, "missing": missing}
    return {"available": True, "url": url, "ingress": entry, "ha_token": token}


def mark_remote(device, headers, ip):
    """Sparar senaste anropet via Nabu Casa så att webbadminen kan visa att vägen fungerar."""
    now = time.time()
    key = device["id"]
    if now - _last_remote.get(key, 0) < 60:
        return
    _last_remote[key] = now
    db.set_setting("remote_last", {
        "at": db.iso(), "device": device["name"], "ip": ip,
        "user": headers.get("X-Remote-User-Name") or headers.get("X-Remote-User-Display-Name"),
        "user_header": bool(headers.get("X-Remote-User-Id")),
    })


# ====================================================================
# Testa hela vägen, precis som telefonen gör
# ====================================================================
def test_path(context=None):
    if context is None and os.environ.get("REMOTE_CA_FILE"):  # bara i testerna
        context = ssl.create_default_context(cafile=os.environ["REMOTE_CA_FILE"])
    cfg = device_config()
    steps = []

    def done(ok, summary):
        result = {"ok": ok, "summary": summary, "steps": steps, "at": db.iso()}
        db.set_setting("remote_test", {"ok": ok, "summary": summary, "at": result["at"]})
        return result

    if not cfg["available"]:
        return done(False, "Saknas: " + ", ".join(cfg["missing"]) + ".")
    url, token, entry = cfg["url"], cfg["ha_token"], cfg["ingress"]
    ws_url = "wss://" + url[len("https://"):] + "/api/websocket"
    try:
        me, session = ha_session(ws_url, token, [
            {"type": "auth/current_user"},
            {"type": "supervisor/api", "endpoint": "/ingress/session", "method": "post"},
        ], timeout=15, context=context)
    except (OSError, WsError, ValueError) as exc:
        steps.append({"ok": False, "text": f"Inloggning via {url}: {exc}"})
        return done(False, "Kunde inte logga in i Home Assistant via Nabu Casa.")
    who = me.get("name") or "?"
    steps.append({"ok": True, "text": f"Inloggad via Nabu Casa som {who}."})
    if me.get("is_admin"):
        steps.append({"ok": False, "text": f"{who} är administratör. Skapa en vanlig användare för appen "
                                           f"(se instruktionen) och byt nyckel."})
    session_id = (session or {}).get("session")
    if not session_id:
        steps.append({"ok": False, "text": "Fick ingen ingress-session."})
        return done(False, "Home Assistant gav ingen ingress-session.")
    steps.append({"ok": True, "text": "Ingress-session skapad."})
    request = urllib.request.Request(url + entry + "/device/ping",
                                     headers={"Cookie": f"ingress_session={session_id}",
                                              "X-TVHub-Token": "test"})
    try:
        handler = urllib.request.HTTPSHandler(context=context) if context else urllib.request.HTTPSHandler()
        opener = urllib.request.build_opener(handler)
        with opener.open(request, timeout=15) as resp:
            body = json.loads(resp.read().decode() or "{}")
        if body.get("app") != "tvhub":
            raise WsError("svaret kom inte från TV Hub")
        steps.append({"ok": True, "text": "TV Hub svarade via Nabu Casa."})
    except urllib.error.HTTPError as exc:
        hint = " Tillägget kräver att användaren får använda tillägg (uppdatera tillägget och starta om det)." \
            if exc.code in (401, 403) else ""
        steps.append({"ok": False, "text": f"TV Hub via Nabu Casa: HTTP {exc.code}.{hint}"})
        return done(False, "Home Assistant släppte inte igenom anropet till TV Hub.")
    except Exception as exc:
        steps.append({"ok": False, "text": f"TV Hub via Nabu Casa: {exc}"})
        return done(False, "TV Hub svarade inte via Nabu Casa.")
    if me.get("is_admin"):
        return done(False, "Vägen fungerar, men nyckeln tillhör en administratör. Byt till en vanlig användare.")
    return done(True, "Allt fungerar. Telefonen kan användas utanför hemmet.")
