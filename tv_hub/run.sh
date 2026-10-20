#!/usr/bin/with-contenv sh
# with-contenv: ger programmet Supervisor-nyckeln (behövs för Home Assistant-anrop och Nabu Casa).
# Inställningar läses av appen från /data/options.json.
# Databasen ligger i /data och följer med i Home Assistants säkerhetskopior.
export DATA_DIR=/data
export PYTHONUNBUFFERED=1
exec python3 /app/main.py
