"""Gränser mot gissning och missbruk: försöksräknare som är säkra vid parallella anrop,
och en kontroll som hindrar att servern hämtar adresser i hemnätet (SSRF).
"""
import ipaddress
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class AttemptLimiter:
    """Räknar försök per nyckel. Ett försök räknas redan när det reserveras, innan den dyra kontrollen
    (hashning, väntan) körs, så att parallella anrop inte kan gissa mer än tillåtet.

    reserve(key) -> None om försöket får göras, annars antal sekunder kvar av spärren.
    success(key) nollställer. Fel behöver inget anrop: försöket är redan räknat.
    """

    def __init__(self, max_attempts, window_s):
        self.max_attempts = max_attempts
        self.window_s = window_s
        self._lock = threading.Lock()
        self._state = {}  # key -> (antal, första försöket)

    def reserve(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            count, first = self._state.get(key, (0, now))
            if now - first > self.window_s:
                count, first = 0, now
            if count >= self.max_attempts:
                return max(1, int(self.window_s - (now - first)))
            self._state[key] = (count + 1, first)
            if len(self._state) > 2000:  # håll minnet nere
                for k in [k for k, (_, f) in self._state.items() if now - f > self.window_s]:
                    del self._state[k]
            return None

    def success(self, key):
        with self._lock:
            self._state.pop(key, None)

    def clear(self):
        with self._lock:
            self._state.clear()


# ---------------------------------------------------------------- SSRF

class BlockedAddress(Exception):
    """Adressen pekar på något internt (hemnätet, loopback, tjänster i Home Assistant)."""


def allow_private():
    """Bara för tester mot en lokal server."""
    return os.environ.get("TVHUB_ALLOW_PRIVATE_URLS") == "1"


def check_public(url, allow_hosts=()):
    """Kastar BlockedAddress om url inte är en vanlig http(s)-adress till en publik värd.
    allow_hosts: "värd:port" som alltid får användas (t.ex. källans egen adress)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedAddress("Ogiltig adress")
    host = parts.hostname.lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if allow_private() or f"{host}:{port}" in {h.lower() for h in allow_hosts if h}:
        return
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise BlockedAddress("Värden hittades inte")
    if not infos:
        raise BlockedAddress("Värden hittades inte")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise BlockedAddress("Adressen är inte publik")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_public(url, headers, timeout, allow_hosts=(), max_redirects=3):
    """Öppnar en publik adress. Omdirigeringar följs för hand och varje steg kontrolleras.
    Returnerar ett svarsobjekt (som urlopen). Kastar BlockedAddress eller urllib-fel.

    Kvarstående begränsning: värdnamnet slås upp igen när anslutningen görs (DNS-ompekning mellan
    kontrollen och anslutningen stoppas inte)."""
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(max_redirects + 1):
        check_public(current, allow_hosts)
        request = urllib.request.Request(current, headers=headers)
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                current = urllib.parse.urljoin(current, exc.headers["Location"])
                exc.close()
                continue
            raise
    raise BlockedAddress("För många omdirigeringar")
