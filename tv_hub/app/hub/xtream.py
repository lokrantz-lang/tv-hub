"""Klient för Xtream Codes API (player_api.php).

Alla anrop returnerar (data, ms) eller kastar XtreamError med en
felklass som diagnostiken kan tolka.
"""
import base64
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import CFG


class XtreamError(Exception):
    """kind: dns, refused, timeout, network, http_<kod>, bad_response,
    auth_failed, account_<status>"""

    def __init__(self, kind, message, ms=None):
        super().__init__(message)
        self.kind = kind
        self.ms = ms


def normalize_base(url):
    """Accepterar 'http://ip:port', 'http://ip:port/', '.../player_api.php',
    '.../get.php?...' och '.../c/' och returnerar bas-URL utan snedstreck."""
    url = url.strip()
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path
    for suffix in ("/player_api.php", "/get.php", "/xmltv.php", "/c/", "/c"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def classify(exc):
    """Översätter nätverksfel till en felklass."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if "timed out" in str(reason).lower():
        return "timeout"
    if isinstance(reason, (ConnectionResetError, BrokenPipeError)):
        return "reset"
    return "network"


def tcp_probe(host, port, timeout):
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.perf_counter() - start) * 1000), None
    except OSError as exc:
        return False, None, f"{classify(exc)}: {exc}"


MAX_API_BYTES = 128 * 1024 * 1024  # största svar från källans API (hela listor kan vara tiotals MB)


class XtreamClient:
    def __init__(self, url, username, password, timeout=None):
        self.base = normalize_base(url)
        self.username = username
        self.password = password
        self.timeout = timeout or CFG["timeout"]
        parsed = urllib.parse.urlsplit(self.base)
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # ---------- låg nivå ----------
    def _open(self, url, timeout):
        request = urllib.request.Request(
            url, headers={"User-Agent": CFG["user_agent"], "Accept": "*/*"}
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def api(self, action=None, timeout=None, **params):
        query = {"username": self.username, "password": self.password}
        if action:
            query["action"] = action
        query.update({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}/player_api.php?{urllib.parse.urlencode(query)}"
        start = time.perf_counter()
        try:
            with self._open(url, timeout or self.timeout) as response:
                body = response.read(MAX_API_BYTES + 1)
                if len(body) > MAX_API_BYTES:
                    raise XtreamError("too_large", "Svaret från källan är för stort", 0)
        except urllib.error.HTTPError as exc:
            ms = round((time.perf_counter() - start) * 1000)
            raise XtreamError(f"http_{exc.code}", f"HTTP {exc.code}", ms) from None
        except (urllib.error.URLError, OSError) as exc:
            ms = round((time.perf_counter() - start) * 1000)
            raise XtreamError(classify(exc), str(getattr(exc, "reason", exc)), ms) from None
        ms = round((time.perf_counter() - start) * 1000)
        try:
            data = json.loads(body.decode("utf-8", errors="replace") or "null")
        except ValueError:
            snippet = body[:80].decode("utf-8", errors="replace")
            raise XtreamError("bad_response", f"Inte JSON: {snippet!r}", ms) from None
        return data, ms

    # ---------- konto ----------
    def login(self):
        """Returnerar (info, ms). info innehåller kontostatus."""
        data, ms = self.api()
        if not isinstance(data, dict) or "user_info" not in data:
            raise XtreamError("bad_response", "Svaret saknar user_info", ms)
        user = data.get("user_info") or {}
        if str(user.get("auth", "0")) != "1":
            raise XtreamError("auth_failed", "Inloggningen nekades", ms)
        status = str(user.get("status") or "")
        info = {
            "status": status,
            "exp_date": _to_int(user.get("exp_date")),
            "max_connections": _to_int(user.get("max_connections")),
            "active_cons": _to_int(user.get("active_cons")),
            "formats": user.get("allowed_output_formats") or [],
            "server": data.get("server_info") or {},
        }
        if status and status.lower() != "active":
            raise XtreamError(f"account_{status.lower()}", f"Kontostatus: {status}", ms)
        return info, ms

    # ---------- katalog ----------
    def list_action(self, action, timeout=None):
        data, ms = self.api(action, timeout=timeout)
        if isinstance(data, dict) and not data:
            data = []
        if not isinstance(data, list):
            raise XtreamError("bad_response", f"{action}: förväntade en lista", ms)
        return data, ms

    def series_info(self, series_id):
        data, _ = self.api("get_series_info", series_id=series_id)
        return data if isinstance(data, dict) else {}

    def short_epg(self, stream_id, limit=6):
        data, _ = self.api("get_short_epg", stream_id=stream_id, limit=limit)
        listings = (data or {}).get("epg_listings") or []
        out = []
        for item in listings:
            out.append(
                {
                    "title": _b64(item.get("title")),
                    "description": _b64(item.get("description")),
                    "start": _to_int(item.get("start_timestamp")),
                    "end": _to_int(item.get("stop_timestamp")),
                }
            )
        return out

    def vod_info(self, vod_id, timeout=None):
        data, _ = self.api("get_vod_info", timeout=timeout, vod_id=vod_id)
        if not isinstance(data, dict):
            raise XtreamError("bad_response", "get_vod_info: förväntade ett objekt")
        info = data.get("info")
        return info if isinstance(info, dict) else {}

    def full_epg(self, stream_id):
        """Alla program för en kanal som källan har (bakåt och framåt), med catch-up-flagga."""
        data, _ = self.api("get_simple_data_table", stream_id=stream_id)
        listings = (data or {}).get("epg_listings") or [] if isinstance(data, dict) else []
        out = []
        for item in listings:
            start, end = _to_int(item.get("start_timestamp")), _to_int(item.get("stop_timestamp"))
            if not start or not end:
                continue
            out.append({
                "title": _b64(item.get("title")),
                "description": _b64(item.get("description")),
                "start": start,
                "end": end,
                "archive": str(item.get("has_archive", "0")) == "1",
            })
        out.sort(key=lambda p: p["start"])
        return out

    # ---------- uppspelning ----------
    def stream_url(self, kind, item_id, ext):
        user = urllib.parse.quote(self.username, safe="")
        pw = urllib.parse.quote(self.password, safe="")
        folder = {"live": "live", "vod": "movie", "episode": "series"}[kind]
        return f"{self.base}/{folder}/{user}/{pw}/{item_id}.{ext}"

    def timeshift_url(self, stream_id, start_local, minutes, ext="ts"):
        """Catch-up. start_local = starttid i källans tidszon, format ÅÅÅÅ-MM-DD:TT-MM."""
        user = urllib.parse.quote(self.username, safe="")
        pw = urllib.parse.quote(self.password, safe="")
        return f"{self.base}/timeshift/{user}/{pw}/{int(minutes)}/{start_local}/{stream_id}.{ext}"


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _b64(value):
    if not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return str(value)
