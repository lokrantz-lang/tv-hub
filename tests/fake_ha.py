#!/usr/bin/env python3
"""Simulerad Home Assistant för testerna av vägen via Nabu Casa.

  fake_ha.py <tls-port> <ws-port> <admin-port> <cert> <key>

tls-port  "Nabu Casa": wss://.../api/websocket och https://.../api/hassio_ingress/<tok>/...
          (ingress-proxyn skickar vidare till TV Hubs port 8080 med X-Remote-User-*)
ws-port   "Supervisor": ws://.../core/websocket (config/auth/list)

Användare: admin (admin, nyckel "admin-token"), tvhub (vanlig, nyckel "app-token-...").
Supervisor-nyckel: "sup-token". Ingress-nyckel: "INGRESSTOK".
"""
import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import socketserver
import ssl
import struct
import sys
import threading
import time

USERS = [
    {"id": "u-admin", "username": "admin", "name": "Admin", "is_owner": True, "is_active": True,
     "group_ids": ["system-admin"]},
    {"id": "u-tvhub", "username": "tvhub", "name": "TV Hub-appen", "is_owner": False, "is_active": True,
     "group_ids": ["system-users"]},
    {"id": "u-guest", "username": "lisa", "name": "Lisa", "is_owner": False, "is_active": True,
     "group_ids": ["system-users"]},
]
TOKENS = {"admin-token": "u-admin", "app-token-0123456789abcdef": "u-tvhub", "guest-token-0123456789abc": "u-guest"}
SUPERVISOR_TOKEN = "sup-token"
INGRESS = "INGRESSTOK"
SESSIONS = {}  # session -> (user_id, valid_until)
SUBSCRIBERS = []  # (conn, id) för subscribe_events tv_hub_command
SCRIPTS = {}  # skript skapade via config/script/config
STATES = [
    {"entity_id": "media_player.vardagsrum_tv", "state": "off",
     "attributes": {"friendly_name": "Samsung 65", "source_list": ["TV", "HDMI 1", "HDMI 2"]}},
    {"entity_id": "light.kok", "state": "on", "attributes": {"friendly_name": "Kök"}},
]
CALLS = []
NOTIFIES = []
STATS = {"sessions": 0, "proxied": 0}
ADMIN_PORT = 0


def user(uid):
    return next(u for u in USERS if u["id"] == uid)


class Conn:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_head(self):
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        return method, path, headers

    # --- websocket (serversidan: ramar från klienten MÅSTE vara maskade) ---
    def ws_recv(self):
        b0, b1 = self.read_exact(2)
        opcode = b0 & 0x0F
        if not b1 & 0x80:
            raise ValueError("omaskad ram från klient")
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self.read_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self.read_exact(8))[0]
        mask = self.read_exact(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(self.read_exact(n)))
        if opcode == 0x8:
            raise EOFError
        if not b0 & 0x80:
            raise ValueError("fragmenterat (stöds inte i testet)")
        return json.loads(data.decode())

    def ws_send(self, obj, split=False):
        data = json.dumps(obj).encode()
        if split and len(data) > 4:
            # skicka i två fragment och med en ping emellan, för att testa klienten
            a, b = data[:len(data) // 2], data[len(data) // 2:]
            self.sock.sendall(self._frame(0x1, a, fin=False) + self._frame(0x9, b"hi") + self._frame(0x0, b))
            return
        self.sock.sendall(self._frame(0x1, data))

    @staticmethod
    def _frame(opcode, data, fin=True):
        head = bytearray([(0x80 if fin else 0) | opcode])
        n = len(data)
        if n < 126:
            head.append(n)
        elif n < 65536:
            head.append(126)
            head += struct.pack(">H", n)
        else:
            head.append(127)
            head += struct.pack(">Q", n)
        return bytes(head) + data


def handle_ws(conn, headers, supervisor):
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    conn.sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    conn.ws_send({"type": "auth_required", "ha_version": "2026.10.0"})
    msg = conn.ws_recv()
    token = msg.get("access_token")
    if supervisor:
        uid = "supervisor" if token == SUPERVISOR_TOKEN else None
    else:
        uid = TOKENS.get(token)
    if msg.get("type") != "auth" or not uid:
        conn.ws_send({"type": "auth_invalid", "message": "Invalid access token or password"})
        return
    conn.ws_send({"type": "auth_ok", "ha_version": "2026.10.0"})
    while True:
        msg = conn.ws_recv()
        mid, kind = msg.get("id"), msg.get("type")
        if kind == "subscribe_events" and uid == "supervisor":
            SUBSCRIBERS.append((conn, mid, msg.get("event_type")))
            conn.ws_send({"id": mid, "type": "result", "success": True, "result": None})
        elif kind == "ping":
            conn.ws_send({"id": mid, "type": "pong"})
        elif kind == "config/auth/list" and uid == "supervisor":
            conn.ws_send({"id": mid, "type": "result", "success": True, "result": USERS}, split=True)
        elif kind == "auth/current_user" and uid != "supervisor":
            u = user(uid)
            conn.ws_send({"id": mid, "type": "result", "success": True,
                          "result": {"id": uid, "name": u["name"], "is_owner": u["is_owner"],
                                     "is_admin": "system-admin" in u["group_ids"]}})
        elif kind == "supervisor/api" and msg.get("endpoint") == "/ingress/session" and uid != "supervisor":
            session = secrets.token_hex(64)
            SESSIONS[session] = (uid, time.time() + 900)
            STATS["sessions"] += 1
            # Home Assistant skickar ofta ett event före resultatet; klienten ska vänta på rätt id.
            conn.ws_send({"id": mid + 1000, "type": "event", "event": {}})
            conn.ws_send({"id": mid, "type": "result", "success": True, "result": {"session": session}})
        else:
            conn.ws_send({"id": mid, "type": "result", "success": False,
                          "error": {"code": "unauthorized", "message": "Unauthorized"}})


def handle_core(conn, method, path, headers):
    """Supervisorns proxy till Home Assistants REST-API (/core/api/...)."""
    if headers.get("authorization") != f"Bearer {SUPERVISOR_TOKEN}":
        return respond(conn, 401, b'{"message":"401"}')
    length = int(headers.get("content-length") or 0)
    body = json.loads(conn.read_exact(length) or b"null") if length else None
    sub = path[len("/core/api/"):]
    CALLS.append((method, sub))
    if sub.startswith("config/script/config/"):
        sid = sub.rsplit("/", 1)[1]
        if method == "POST":
            if not isinstance(body, dict) or "sequence" not in body or "alias" not in body:
                return respond(conn, 400, b'{"message":"Message malformed"}')
            SCRIPTS[sid] = body
            return respond(conn, 200, b'{"result":"ok"}')
        if method == "DELETE":
            SCRIPTS.pop(sid, None)
            return respond(conn, 200, b'{"result":"ok"}')
    if sub == "states" and method == "GET":
        return respond(conn, 200, json.dumps(STATES).encode())
    if sub == "services" and method == "GET":
        return respond(conn, 200, json.dumps([
            {"domain": "light", "services": {"turn_on": {}}},
            {"domain": "notify", "services": {"mobile_app_telefon": {}, "persistent_notification": {}, "notify": {}}},
        ]).encode())
    if sub.startswith(("services/", "events/")) and method == "POST":
        if sub.startswith("services/notify/"):
            NOTIFIES.append((sub, body))
        return respond(conn, 200, b"[]")
    return respond(conn, 404, b'{"message":"not found"}')


def handle_http(conn, method, path, headers):
    if path.startswith("/__fire"):
        cid = path.split("=", 1)[1] if "=" in path else "0"
        sent = 0
        for c, sid, etype in list(SUBSCRIBERS):
            try:
                c.ws_send({"id": sid, "type": "event", "event": {"event_type": etype, "data": {"command_id": int(cid)}}})
                sent += 1
            except OSError:
                SUBSCRIBERS.remove((c, sid, etype))
        return respond(conn, 200, json.dumps({"sent": sent}).encode())
    if path == "/__notifies":
        return respond(conn, 200, json.dumps(NOTIFIES).encode())
    if path == "/__scripts":
        return respond(conn, 200, json.dumps(SCRIPTS).encode())
    if path == "/__stats":
        return respond(conn, 200, json.dumps(STATS).encode())
    if path == "/__expire":
        for k in list(SESSIONS):
            SESSIONS[k] = (SESSIONS[k][0], 0)
        return respond(conn, 200, b"{}")
    prefix = f"/api/hassio_ingress/{INGRESS}/"
    if not path.startswith(prefix):
        return respond(conn, 404, b'{"message":"not found"}')
    cookies = dict(c.strip().split("=", 1) for c in headers.get("cookie", "").split(";") if "=" in c)
    entry = SESSIONS.get(cookies.get("ingress_session", ""))
    if not entry or entry[1] < time.time():
        return respond(conn, 401, b"401: Unauthorized", ctype="text/plain")
    SESSIONS[cookies["ingress_session"]] = (entry[0], time.time() + 900)
    u = user(entry[0])
    length = int(headers.get("content-length") or 0)
    body = conn.read_exact(length) if length else None
    fwd = {k: v for k, v in headers.items()
           if k not in ("host", "content-length", "connection", "x-remote-user-id", "x-remote-user-name",
                        "x-remote-user-display-name", "authorization")}
    fwd.update({"X-Remote-User-Id": u["id"], "X-Remote-User-Name": u["username"],
                "X-Remote-User-Display-Name": u["name"], "X-Forwarded-For": "203.0.113.7, 172.30.32.1"})
    up = http.client.HTTPConnection("127.0.0.1", ADMIN_PORT, timeout=30)
    up.request(method, "/" + path[len(prefix):], body=body, headers=fwd)
    res = up.getresponse()
    data = res.read()
    STATS["proxied"] += 1
    respond(conn, res.status, data, ctype=res.getheader("Content-Type") or "application/octet-stream")


def respond(conn, status, body, ctype="application/json"):
    conn.sock.sendall((f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                       "Connection: close\r\n\r\n").encode() + body)


def serve(port, context, supervisor):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            sock = self.request
            if context:
                try:
                    sock = context.wrap_socket(sock, server_side=True)
                except (ssl.SSLError, OSError):
                    return
            conn = Conn(sock)
            try:
                method, path, headers = conn.read_head()
                if headers.get("upgrade", "").lower() == "websocket":
                    want = "/core/websocket" if supervisor else "/api/websocket"
                    if path != want:
                        return respond(conn, 404, b"{}")
                    handle_ws(conn, headers, supervisor)
                elif supervisor and path.startswith("/core/api/"):
                    handle_core(conn, method, path, headers)
                elif not supervisor:
                    handle_http(conn, method, path, headers)
            except (EOFError, ConnectionError, OSError, ValueError):
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    tls_port, ws_port, ADMIN_PORT = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(sys.argv[4], sys.argv[5])
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    serve(tls_port, ctx, supervisor=False)
    serve(ws_port, None, supervisor=True)
    while True:
        time.sleep(3600)
