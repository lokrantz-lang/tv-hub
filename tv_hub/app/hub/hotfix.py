"""Hemfix: lägg över en ny version av tilläggets kod utan att gå via GitHub.

Webbadminen tar emot en zipfil (samma som levereras för varje version). Innehållet under tv_hub/app/ (eller app/)
kontrolleras, provas i en egen process och läggs i /data/hotfix/app. run.sh startar därefter den koden i stället för
den som ligger i tilläggets avbildning. Gäller bara tilläggets egen kod och webbadmin, inte appen på boxarna.

Skydd: bara tillåtna filtyper och sökvägar, storleksgräns, syntaxkontroll och provimport före bytet. En hemfix som
inte håller sig igång i 30 sekunder kastas vid nästa start. Installerar du en nyare version av tillägget på vanligt
sätt tas hemfixen bort automatiskt.
"""
import base64
import io
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

from .config import CFG

ALLOWED_EXT = {".py", ".html", ".js", ".css", ".json", ".png", ".svg", ".ico", ".txt", ".md", ".webmanifest"}
MAX_ZIP = 20 * 1024 * 1024
MAX_UNPACKED = 40 * 1024 * 1024
MAX_FILES = 600
VERSION_RE = re.compile(r'^VERSION\s*=\s*"([^"]+)"', re.M)


class HotfixError(Exception):
    pass


def _root():
    return os.path.join(CFG["data_dir"], "hotfix")


def _running_app():
    """Katalogen koden körs från (hub/..)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _image_app():
    return os.environ.get("HOTFIX_IMAGE_APP") or "/app"


def _version_in(app_dir):
    try:
        with open(os.path.join(app_dir, "hub", "__init__.py"), encoding="utf-8") as fh:
            m = VERSION_RE.search(fh.read())
        return m.group(1) if m else None
    except OSError:
        return None


def status():
    root = _root()
    active = os.path.isdir(os.path.join(root, "app"))
    return {
        "running_from_hotfix": os.path.abspath(_running_app()).startswith(os.path.abspath(root) + os.sep),
        "active": active,
        "hotfix_version": _version_in(os.path.join(root, "app")) if active else None,
        "image_version": _version_in(_image_app()),
        "confirmed": os.path.exists(os.path.join(root, "OK")) if active else None,
    }


def _clean_members(archive):
    """Filer som ska läggas över: {relativ sökväg under app/: ZipInfo}. Avvisar allt misstänkt."""
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if not infos or len(infos) > MAX_FILES * 4:
        raise HotfixError("Zipfilen är tom eller har för många filer.")
    prefix = None
    for info in infos:
        parts = info.filename.replace("\\", "/").split("/")
        for k in range(len(parts) - 1):
            if parts[k] == "app" and (k == 0 or parts[k - 1] == "tv_hub"):
                prefix = "/".join(parts[:k + 1]) + "/"
                break
        if prefix:
            break
    if not prefix:
        raise HotfixError("Hittade ingen mapp tv_hub/app i zipfilen. Använd zipfilen jag levererat.")
    out, total = {}, 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        if not name.startswith(prefix):
            continue
        rel = posixpath.normpath(name[len(prefix):])
        if rel.startswith("/") or rel.startswith("..") or "/../" in f"/{rel}/" or "\x00" in rel:
            raise HotfixError(f"Otillåten sökväg: {name[:80]}")
        if "__pycache__" in rel.split("/"):
            continue
        if os.path.splitext(rel)[1].lower() not in ALLOWED_EXT:
            raise HotfixError(f"Otillåten filtyp: {rel[:80]}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise HotfixError(f"Symbolisk länk nekas: {rel[:80]}")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise HotfixError("Zipfilen är för stor när den packas upp.")
        out[rel] = info
    if len(out) > MAX_FILES:
        raise HotfixError("För många filer.")
    if "hub/__init__.py" not in out and "main.py" not in out:
        raise HotfixError("Zipfilen innehåller inte tilläggets kod (hub/__init__.py).")
    return out


def _check(app_dir):
    """Syntaxkontroll och provimport i en egen process. Kastar HotfixError."""
    for dirpath, _dirs, files in os.walk(app_dir):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(dirpath, f)
                try:
                    with open(path, "rb") as fh:
                        compile(fh.read(), path, "exec")
                except SyntaxError as exc:
                    raise HotfixError(f"Syntaxfel i {os.path.relpath(path, app_dir)} rad {exc.lineno}: {exc.msg}")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import hub, hub.server, hub.db; print(hub.VERSION)")
    env = dict(os.environ, DATA_DIR=os.path.join(CFG["data_dir"], "hotfix-probe"), PYTHONDONTWRITEBYTECODE="1")
    os.makedirs(env["DATA_DIR"], exist_ok=True)
    try:
        res = subprocess.run([sys.executable, "-I", "-c", code, app_dir], capture_output=True, text=True,
                             timeout=30, env=env, cwd=app_dir)
    except subprocess.TimeoutExpired:
        raise HotfixError("Provimporten tog för lång tid.")
    finally:
        shutil.rmtree(env["DATA_DIR"], ignore_errors=True)
    if res.returncode != 0:
        raise HotfixError("Koden går inte att starta: " + (res.stderr.strip().splitlines() or ["okänt fel"])[-1][:200])


def install(data_b64):
    """Tar emot zipfilen (base64), kontrollerar och aktiverar. Returnerar sammanfattning; omstart sker separat."""
    try:
        raw = base64.b64decode(data_b64, validate=False)
    except Exception:
        raise HotfixError("Filen kunde inte läsas.")
    if len(raw) > MAX_ZIP:
        raise HotfixError("Zipfilen är för stor.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HotfixError("Det här är ingen zipfil.")
    members = _clean_members(archive)
    root = _root()
    staging = root + ".ny"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        app_dir = os.path.join(staging, "app")
        shutil.copytree(_running_app(), app_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for rel, info in members.items():
            target = os.path.join(app_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        _check(app_dir)
        version = _version_in(app_dir)
        with open(os.path.join(staging, "BASE"), "w") as fh:
            fh.write(_version_in(_image_app()) or "")
        for marker in ("TRIED", "STARTED"):
            with open(os.path.join(staging, marker), "w") as fh:
                fh.write(str(int(time.time())))
        shutil.rmtree(root + ".gammal", ignore_errors=True)
        if os.path.exists(root):
            os.rename(root, root + ".gammal")
        os.rename(staging, root)
        shutil.rmtree(root + ".gammal", ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"version": version, "files": len(members), "previous": _version_in(_running_app())}


def remove():
    shutil.rmtree(_root(), ignore_errors=True)


def mark_healthy(delay=30):
    """Anropas vid start: efter en halv minut utan krasch bekräftas hemfixen."""
    root = _root()
    if not status()["running_from_hotfix"]:
        return

    def run():
        time.sleep(delay)
        try:
            with open(os.path.join(root, "OK"), "w") as fh:
                fh.write("ok")
        except OSError:
            pass
    threading.Thread(target=run, daemon=True, name="hotfix-ok").start()


def restart_soon(delay=2.0):
    """Startar om Python-processen på plats (samma process-id) efter att svaret hunnit skickas."""
    if os.environ.get("HOTFIX_NO_EXEC"):
        return False
    app = os.path.join(_root(), "app")
    main = os.path.join(app if os.path.isdir(app) else _image_app(), "main.py")

    def run():
        time.sleep(delay)
        os.execv(sys.executable, [sys.executable, main])
    threading.Thread(target=run, daemon=True, name="hotfix-restart").start()
    return True
