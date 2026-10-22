"""SQLite-lager. En anslutning, ett lås, WAL-läge."""
import contextlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .config import CFG

_lock = threading.RLock()
_conn = None

SCHEMA = [
    # version 1
    """
    CREATE TABLE portals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        url TEXT NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        password TEXT NOT NULL DEFAULT '',
        priority INTEGER NOT NULL DEFAULT 100,
        enabled INTEGER NOT NULL DEFAULT 1,
        created TEXT NOT NULL
    );
    CREATE TABLE checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        portal_id INTEGER NOT NULL,
        tcp_ok INTEGER NOT NULL,
        connect_ms INTEGER,
        api_ok INTEGER NOT NULL DEFAULT 0,
        api_ms INTEGER,
        error_kind TEXT,
        error TEXT,
        account_status TEXT,
        exp_date INTEGER,
        max_connections INTEGER,
        active_cons INTEGER,
        formats TEXT
    );
    CREATE INDEX idx_checks_portal ON checks(portal_id, id);
    CREATE TABLE net_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        ok INTEGER NOT NULL,
        ms INTEGER,
        detail TEXT
    );
    CREATE TABLE categories (
        type TEXT NOT NULL,
        category_id TEXT NOT NULL,
        name TEXT NOT NULL,
        sort INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (type, category_id)
    );
    CREATE TABLE items (
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        name TEXT NOT NULL,
        name_norm TEXT NOT NULL,
        category_id TEXT,
        icon TEXT,
        ext TEXT,
        rating TEXT,
        added INTEGER,
        num INTEGER,
        epg_id TEXT,
        PRIMARY KEY (type, item_id)
    );
    CREATE INDEX idx_items_cat ON items(type, category_id, num);
    CREATE TABLE sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started TEXT NOT NULL,
        finished TEXT,
        portal_id INTEGER,
        status TEXT NOT NULL,
        trigger TEXT,
        detail TEXT
    );
    CREATE TABLE favorites (
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        added TEXT NOT NULL,
        PRIMARY KEY (type, item_id)
    );
    CREATE TABLE groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        sort INTEGER NOT NULL DEFAULT 100,
        created TEXT NOT NULL
    );
    CREATE TABLE group_items (
        group_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        sort INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (group_id, type, item_id)
    );
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        model TEXT,
        app_version TEXT,
        token_hash TEXT NOT NULL UNIQUE,
        created TEXT NOT NULL,
        last_seen TEXT,
        last_ip TEXT
    );
    CREATE TABLE pair_codes (
        code TEXT PRIMARY KEY,
        expires TEXT NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        device_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        item_name TEXT,
        portal_id INTEGER,
        started TEXT NOT NULL,
        last_heartbeat TEXT NOT NULL,
        ended TEXT,
        end_reason TEXT,
        stop_requested INTEGER NOT NULL DEFAULT 0,
        metrics TEXT
    );
    CREATE INDEX idx_sessions_active ON sessions(ended, last_heartbeat);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        device_id INTEGER,
        session_id TEXT,
        kind TEXT NOT NULL,
        detail TEXT
    );
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    # version 2: markering av vuxeninnehåll
    """
    ALTER TABLE categories ADD COLUMN adult INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE items ADD COLUMN adult INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX idx_items_adult ON items(type, adult);
    """,
    # version 3: metadata, dolda kategorier, profiler, fortsätt titta, sparade filter, bevakningar
    """
    ALTER TABLE items ADD COLUMN year INTEGER;
    ALTER TABLE items ADD COLUMN rating_num REAL;
    ALTER TABLE items ADD COLUMN archive_days INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE item_info (
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        state INTEGER NOT NULL DEFAULT 0,
        fetched TEXT,
        duration_min INTEGER,
        genre TEXT,
        plot TEXT,
        actors TEXT,
        director TEXT,
        release TEXT,
        poster TEXT,
        backdrop TEXT,
        trailer TEXT,
        country TEXT,
        year INTEGER,
        rating REAL,
        PRIMARY KEY (type, item_id)
    );
    CREATE TABLE category_prefs (
        type TEXT NOT NULL,
        category_id TEXT NOT NULL,
        hidden INTEGER NOT NULL DEFAULT 0,
        position INTEGER,
        PRIMARY KEY (type, category_id)
    );
    CREATE TABLE saved_filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        adult INTEGER NOT NULL DEFAULT 0,
        params TEXT NOT NULL,
        created TEXT NOT NULL
    );
    CREATE TABLE progress (
        adult INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        series_id TEXT,
        name TEXT,
        icon TEXT,
        ext TEXT,
        position_ms INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        updated TEXT NOT NULL,
        PRIMARY KEY (adult, type, item_id)
    );
    CREATE TABLE watched (
        adult INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        series_id TEXT,
        at TEXT NOT NULL,
        PRIMARY KEY (adult, type, item_id)
    );
    CREATE TABLE watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        type TEXT,
        created TEXT NOT NULL,
        seen TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE watch_hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        name TEXT,
        at TEXT NOT NULL
    );
    ALTER TABLE devices ADD COLUMN allow_adult INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE devices ADD COLUMN only_group_id INTEGER;
    ALTER TABLE sessions ADD COLUMN adult INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE sessions ADD COLUMN series_id TEXT;
    ALTER TABLE sessions ADD COLUMN ext TEXT;
    ALTER TABLE sessions ADD COLUMN icon TEXT;
    CREATE INDEX idx_sessions_device ON sessions(device_id, type, started)
    """,
    # version 4: inspelningar (filerna ligger på boxens USB-minne)
    """
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER NOT NULL,
        stream_id TEXT NOT NULL,
        channel TEXT,
        title TEXT,
        start INTEGER NOT NULL,
        end INTEGER NOT NULL,
        source TEXT NOT NULL,
        adult INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'scheduled',
        session_id TEXT,
        size_bytes INTEGER,
        file TEXT,
        error TEXT,
        created TEXT NOT NULL,
        updated TEXT NOT NULL
    );
    CREATE INDEX idx_recordings_device ON recordings(device_id, status)
    """,
    # version 5: eget tilläggsdatum och manuell vuxenmarkering av kategorier
    """
    ALTER TABLE items ADD COLUMN first_seen INTEGER;
    ALTER TABLE category_prefs ADD COLUMN adult_override INTEGER
    """,
    # version 6: inspelning (nedladdning) av filmer och avsnitt
    """
    ALTER TABLE recordings ADD COLUMN ext TEXT;
    ALTER TABLE recordings ADD COLUMN series_id TEXT
    """,
    # version 7: favoritavsnitt (avsnitt finns inte i katalogen, så namn och bild sparas här)
    """
    CREATE TABLE fav_episodes (
        adult INTEGER NOT NULL DEFAULT 0,
        episode_id TEXT NOT NULL,
        series_id TEXT NOT NULL,
        title TEXT,
        ext TEXT,
        icon TEXT,
        added TEXT NOT NULL,
        PRIMARY KEY (adult, episode_id)
    )
    """,
    # version 8: Filmkväll. Tittarlägen, tummar, logg över sett, Vill se, dolda förslag, AI-förslag.
    # Allt gäller bara vanligt läge (aldrig vuxeninnehåll). work_key slår ihop olika versioner av samma titel.
    """
    CREATE TABLE viewers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        taste TEXT,
        rules TEXT NOT NULL DEFAULT '{}',
        created TEXT NOT NULL
    );
    INSERT INTO viewers (name, position, rules, created) VALUES ('Själv', 1, '{}', datetime('now'));
    INSERT INTO viewers (name, position, rules, created)
        VALUES ('Med döttrarna', 2, '{"kids": true, "rewatch_days": 90}', datetime('now'));
    INSERT INTO viewers (name, position, rules, created) VALUES ('Med vuxen', 3, '{}', datetime('now'));
    CREATE TABLE ratings (
        viewer_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        work_key TEXT NOT NULL,
        item_id TEXT NOT NULL,
        title TEXT,
        value INTEGER NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (viewer_id, type, work_key)
    );
    CREATE TABLE seen_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        viewer_id INTEGER,
        type TEXT NOT NULL,
        work_key TEXT NOT NULL,
        item_id TEXT NOT NULL,
        title TEXT,
        at TEXT NOT NULL,
        source TEXT NOT NULL,
        device_id INTEGER
    );
    CREATE INDEX idx_seen_work ON seen_log(type, work_key);
    CREATE TABLE wishlist (
        type TEXT NOT NULL,
        work_key TEXT NOT NULL,
        item_id TEXT NOT NULL,
        title TEXT,
        viewer_id INTEGER,
        added TEXT NOT NULL,
        PRIMARY KEY (type, work_key)
    );
    CREATE TABLE hidden_picks (
        viewer_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        work_key TEXT NOT NULL,
        until INTEGER,
        PRIMARY KEY (viewer_id, type, work_key)
    );
    CREATE TABLE ai_picks (
        viewer_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        created INTEGER NOT NULL,
        source TEXT NOT NULL,
        picks TEXT NOT NULL,
        PRIMARY KEY (viewer_id, type)
    );
    ALTER TABLE sessions ADD COLUMN viewer_id INTEGER
    """,
    # version 9: egna visningsnamn på kategorier, röstkommandon (fjärrstart via Home Assistant)
    """
    ALTER TABLE category_prefs ADD COLUMN display_name TEXT;
    CREATE TABLE voice_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        device_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        params TEXT NOT NULL DEFAULT '{}',
        script_id TEXT,
        script_status TEXT,
        created TEXT NOT NULL,
        updated TEXT NOT NULL
    )
    """,
    # version 10: påminnelser om program
    """
    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER NOT NULL,
        stream_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        title TEXT NOT NULL,
        start INTEGER NOT NULL,
        end INTEGER NOT NULL,
        adult INTEGER NOT NULL DEFAULT 0,
        sent INTEGER NOT NULL DEFAULT 0,
        created TEXT NOT NULL
    );
    CREATE INDEX reminders_start ON reminders (sent, start)
    """,
    # version 11: profiler (tittarlägena blir profiler), PIN per box, Mina program.
    # Favoriter, Fortsätt titta, sedda och favoritavsnitt blir per profil. Allt som fanns blir den första
    # profilens (Själv -> Henrik).
    """
    ALTER TABLE viewers ADD COLUMN color TEXT;
    ALTER TABLE viewers ADD COLUMN emoji TEXT;
    ALTER TABLE viewers ADD COLUMN pin_salt TEXT;
    ALTER TABLE viewers ADD COLUMN pin_hash TEXT;
    ALTER TABLE viewers ADD COLUMN allow_adult INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE viewers ADD COLUMN guest INTEGER NOT NULL DEFAULT 0;
    UPDATE viewers SET name = 'Henrik', color = '#3B82F6' WHERE name = 'Själv';
    UPDATE viewers SET name = 'Tillsammans', color = '#F59E0B' WHERE name = 'Med döttrarna';
    UPDATE viewers SET allow_adult = 1 WHERE id = (SELECT MIN(id) FROM viewers);
    DELETE FROM viewers WHERE name = 'Med vuxen'
        AND NOT EXISTS (SELECT 1 FROM ratings r WHERE r.viewer_id = viewers.id)
        AND NOT EXISTS (SELECT 1 FROM seen_log s WHERE s.viewer_id = viewers.id)
        AND NOT EXISTS (SELECT 1 FROM wishlist w WHERE w.viewer_id = viewers.id);
    INSERT INTO viewers (name, position, rules, created, color) SELECT 'Ida', 4, '{}', datetime('now'), '#EC4899'
        WHERE NOT EXISTS (SELECT 1 FROM viewers WHERE name = 'Ida');
    INSERT INTO viewers (name, position, rules, created, color) SELECT 'Linn', 5, '{}', datetime('now'), '#10B981'
        WHERE NOT EXISTS (SELECT 1 FROM viewers WHERE name = 'Linn');
    INSERT INTO viewers (name, position, rules, created, color, guest) SELECT 'Gäst', 9, '{}', datetime('now'), '#6B7280', 1
        WHERE NOT EXISTS (SELECT 1 FROM viewers WHERE name = 'Gäst');

    CREATE TABLE favorites_p (
        profile_id INTEGER NOT NULL, type TEXT NOT NULL, item_id TEXT NOT NULL, added TEXT NOT NULL,
        PRIMARY KEY (profile_id, type, item_id)
    );
    INSERT INTO favorites_p SELECT (SELECT MIN(id) FROM viewers), type, item_id, added FROM favorites;
    DROP TABLE favorites;
    ALTER TABLE favorites_p RENAME TO favorites;

    CREATE TABLE progress_p (
        profile_id INTEGER NOT NULL, adult INTEGER NOT NULL, type TEXT NOT NULL, item_id TEXT NOT NULL,
        series_id TEXT, name TEXT, icon TEXT, ext TEXT,
        position_ms INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL,
        PRIMARY KEY (profile_id, adult, type, item_id)
    );
    INSERT INTO progress_p SELECT (SELECT MIN(id) FROM viewers), adult, type, item_id, series_id, name, icon, ext,
        position_ms, duration_ms, updated FROM progress;
    DROP TABLE progress;
    ALTER TABLE progress_p RENAME TO progress;

    CREATE TABLE watched_p (
        profile_id INTEGER NOT NULL, adult INTEGER NOT NULL, type TEXT NOT NULL, item_id TEXT NOT NULL,
        series_id TEXT, at TEXT NOT NULL,
        PRIMARY KEY (profile_id, adult, type, item_id)
    );
    INSERT INTO watched_p SELECT (SELECT MIN(id) FROM viewers), adult, type, item_id, series_id, at FROM watched;
    DROP TABLE watched;
    ALTER TABLE watched_p RENAME TO watched;

    CREATE TABLE fav_episodes_p (
        profile_id INTEGER NOT NULL, adult INTEGER NOT NULL DEFAULT 0, episode_id TEXT NOT NULL, series_id TEXT NOT NULL,
        title TEXT, ext TEXT, icon TEXT, added TEXT NOT NULL,
        PRIMARY KEY (profile_id, adult, episode_id)
    );
    INSERT INTO fav_episodes_p SELECT (SELECT MIN(id) FROM viewers), adult, episode_id, series_id, title, ext, icon, added
        FROM fav_episodes;
    DROP TABLE fav_episodes;
    ALTER TABLE fav_episodes_p RENAME TO fav_episodes;

    UPDATE sessions SET viewer_id = (SELECT MIN(id) FROM viewers) WHERE viewer_id IS NULL;
    ALTER TABLE sessions ADD COLUMN catchup_start INTEGER;

    ALTER TABLE devices ADD COLUMN pin_required INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE devices ADD COLUMN default_profile_id INTEGER;
    ALTER TABLE devices ADD COLUMN revert_hours INTEGER NOT NULL DEFAULT 0;
    UPDATE devices SET pin_required = 0, default_profile_id = (SELECT MIN(id) FROM viewers) WHERE lower(name) LIKE '%sovrum%';
    UPDATE devices SET pin_required = 1, revert_hours = 4,
        default_profile_id = (SELECT id FROM viewers WHERE name = 'Tillsammans') WHERE lower(name) LIKE '%vardagsrum%';

    CREATE TABLE my_programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        mode TEXT NOT NULL,
        channel_id TEXT,
        query TEXT,
        times TEXT NOT NULL DEFAULT '[]',
        series_id TEXT,
        position INTEGER NOT NULL DEFAULT 0,
        created TEXT NOT NULL
    )
    """,
    # version 12: programguide för svenska kanaler och sportkanaler (sök, Mina sporter, TV-guiden)
    """
    CREATE TABLE epg (
        epg_key TEXT NOT NULL,
        start INTEGER NOT NULL,
        end INTEGER NOT NULL,
        title TEXT NOT NULL,
        title_norm TEXT NOT NULL,
        description TEXT,
        archive INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (epg_key, start)
    );
    CREATE INDEX idx_epg_start ON epg(start);
    CREATE TABLE epg_scan (
        epg_key TEXT PRIMARY KEY,
        stream_id TEXT NOT NULL,
        scanned INTEGER NOT NULL,
        programs INTEGER NOT NULL DEFAULT 0,
        error TEXT
    );
    CREATE TABLE sport_interests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'sport',
        keywords TEXT NOT NULL DEFAULT '[]',
        exclude TEXT NOT NULL DEFAULT '[]',
        title_only INTEGER NOT NULL DEFAULT 0,
        notify INTEGER NOT NULL DEFAULT 0,
        record_device_id INTEGER,
        position INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created TEXT NOT NULL
    );
    CREATE TABLE sport_hits (
        interest_id INTEGER NOT NULL,
        epg_key TEXT NOT NULL,
        start INTEGER NOT NULL,
        PRIMARY KEY (interest_id, epg_key, start)
    );
    CREATE TABLE sport_done (
        key TEXT PRIMARY KEY,
        at INTEGER NOT NULL
    );
    INSERT INTO sport_interests (name, kind, keywords, exclude, title_only, position, created) VALUES
        ('Golf', 'sport', '["golf", "pga tour", "lpga", "dp world tour", "ryder cup", "solheim cup", "liv golf"]',
         '["minigolf", "discgolf"]', 1, 1, datetime('now')),
        ('Ridsport', 'sport', '["ridsport", "hästhoppning", "hoppning", "dressyr", "fälttävlan", "horse show", "show jumping", "global champions", "equestrian"]',
         '["trav", "galopp", "v75", "v86", "v64", "v65", "travsport"]', 1, 2, datetime('now')),
        ('Norrköping Dolphins', 'team', '["norrköping dolphins", "dolphins + basket", "dolphins + basketligan", "dolphins + norrköping"]',
         '["miami", "nfl"]', 0, 3, datetime('now')),
        ('IFK Norrköping', 'team', '["ifk norrköping", "ifk + norrköping", "norrköping + allsvenskan", "norrköping + damallsvenskan", "norrköping + svenska cupen"]',
         '["dolphins", "basket"]', 0, 4, datetime('now')),
        ('Sverige, fotbollslandslagen', 'team', '["sverige + fotboll", "sweden + football", "sverige + landskamp", "damlandslaget", "herrlandslaget", "sverige + em kval", "sverige + vm kval", "sverige + nations league", "blågult + fotboll"]',
         '["ishockey", "handboll", "bandy", "innebandy", "tre kronor", "damkronorna", "basket", "friidrott", "volleyboll"]', 0, 5, datetime('now'))
    """,
]


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def ago(**kwargs):
    return iso(now() - timedelta(**kwargs))


def later(**kwargs):
    return iso(now() + timedelta(**kwargs))


def parse_iso(value):
    return datetime.fromisoformat(value) if value else None


def connect():
    global _conn
    os.makedirs(CFG["data_dir"], exist_ok=True)
    path = os.path.join(CFG["data_dir"], "tvhub.db")
    _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=OFF")
    version = _conn.execute("PRAGMA user_version").fetchone()[0]
    for index, script in enumerate(SCHEMA, start=1):
        if version < index:
            with tx():
                for statement in script.split(";"):
                    if statement.strip():
                        _conn.execute(statement)
                _conn.execute(f"PRAGMA user_version = {index}")
    return _conn


@contextlib.contextmanager
def tx():
    """Transaktion. Låset hålls hela vägen."""
    with _lock:
        _conn.execute("BEGIN")
        try:
            yield _conn
        except BaseException:
            _conn.execute("ROLLBACK")
            raise
        else:
            _conn.execute("COMMIT")


def q(sql, args=()):
    with _lock:
        return _conn.execute(sql, args).fetchall()


def q1(sql, args=()):
    with _lock:
        return _conn.execute(sql, args).fetchone()


def x(sql, args=()):
    with _lock:
        return _conn.execute(sql, args)


def rows(result):
    return [dict(row) for row in result]


def get_setting(key, default=None):
    row = q1("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key, value):
    x(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def log_event(kind, detail=None, device_id=None, session_id=None):
    x(
        "INSERT INTO events (ts, device_id, session_id, kind, detail) VALUES (?, ?, ?, ?, ?)",
        (iso(), device_id, session_id, kind, json.dumps(detail or {}, ensure_ascii=False)),
    )
