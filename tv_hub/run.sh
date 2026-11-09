#!/usr/bin/with-contenv sh
# with-contenv: ger programmet Supervisor-nyckeln (behövs för Home Assistant-anrop och Nabu Casa).
# Inställningar läses av appen från /data/options.json.
# Databasen ligger i /data och följer med i Home Assistants säkerhetskopior.
export DATA_DIR=/data
export PYTHONUNBUFFERED=1

# Hemfix (zip uppladdad i webbadminen) ligger i /data/hotfix och körs i stället för avbildningens kod.
# Den kastas om tillägget uppdaterats till en annan version, eller om den inte höll sig igång förra gången.
HF=/data/hotfix
if [ -d "$HF/app" ]; then
  IMG=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' /app/hub/__init__.py)
  if [ "$(cat "$HF/BASE" 2>/dev/null)" != "$IMG" ]; then
    echo "[hemfix] ny version av tillägget, tar bort hemfixen"
    rm -rf "$HF"
  elif [ -f "$HF/TRIED" ] && [ ! -f "$HF/OK" ] && [ -f "$HF/STARTED" ]; then
    echo "[hemfix] hemfixen höll inte, tar bort den"
    rm -rf "$HF"
  fi
fi
if [ -d "$HF/app" ]; then
  touch "$HF/STARTED"
  exec python3 "$HF/app/main.py"
fi
exec python3 /app/main.py
