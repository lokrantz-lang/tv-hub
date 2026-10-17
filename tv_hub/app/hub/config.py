"""Inställningar.

Läses i ordningen: standardvärden, /data/options.json (Home Assistant),
miljövariabler. Senare källa vinner.
"""
import json
import os

DEFAULTS = {
    "data_dir": "/data",
    "admin_port": 8080,
    # Ingress-proxyn i Home Assistant ansluter alltid från 172.30.32.2.
    # Admin-API:t svarar bara på dessa adresser. "*" stänger av spärren.
    "admin_allowed_ips": "172.30.32.2,127.0.0.1",
    "device_port": 8099,
    "check_interval": 120,
    "net_check_interval": 60,
    "sync_interval_hours": 12,
    "timeout": 15,
    "sync_timeout": 90,
    "user_agent": "IPTVSmartersPlayer",
    "output_format": "auto",
    "reference_hosts": "1.1.1.1:443,8.8.8.8:443",
    "retention_days": 30,
}

ENV_MAP = {
    "data_dir": "DATA_DIR",
    "admin_port": "ADMIN_PORT",
    "admin_allowed_ips": "ADMIN_ALLOWED_IPS",
    "device_port": "DEVICE_PORT",
    "check_interval": "CHECK_INTERVAL",
    "net_check_interval": "NET_CHECK_INTERVAL",
    "sync_interval_hours": "SYNC_INTERVAL_HOURS",
    "timeout": "TIMEOUT",
    "sync_timeout": "SYNC_TIMEOUT",
    "user_agent": "USER_AGENT",
    "output_format": "OUTPUT_FORMAT",
    "reference_hosts": "REFERENCE_HOSTS",
    "retention_days": "RETENTION_DAYS",
}


def load():
    cfg = dict(DEFAULTS)
    options_path = os.environ.get("OPTIONS_PATH", "/data/options.json")
    if os.path.exists(options_path):
        try:
            with open(options_path, encoding="utf-8") as fh:
                options = json.load(fh)
            for key in DEFAULTS:
                if key in options and options[key] not in (None, ""):
                    cfg[key] = options[key]
        except (OSError, ValueError) as exc:
            print(f"Kunde inte läsa {options_path}: {exc}", flush=True)
    for key, env in ENV_MAP.items():
        if env in os.environ:
            cfg[key] = os.environ[env]
    for key, default in DEFAULTS.items():
        if isinstance(default, int):
            cfg[key] = int(cfg[key])
    if cfg["output_format"] not in ("auto", "ts", "m3u8"):
        cfg["output_format"] = "auto"
    return cfg


CFG = load()
