#!/bin/bash
# Verwijdert het contacten-sync achtergrondprogramma weer volledig
# (stopt de achtergronddienst, verwijdert instellingen en het bewaarde wachtwoord
# uit de sleutelhanger). De contacten en de groep blijven gewoon staan in
# Contacten — die verwijder je zelf daar, als je dat wilt.
set -e

APP_SUPPORT="$HOME/Library/Application Support/InformatiemanagementTrackerSync"
PLIST="$HOME/Library/LaunchAgents/local.informatiemanagement.tracker-contactsync.plist"
LABEL="local.informatiemanagement.tracker-contactsync"
CONFIG_FILE="$APP_SUPPORT/config.json"

echo "== Contacten-sync verwijderen =="

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "Achtergronddienst gestopt en uitgeschakeld."

if [ -f "$CONFIG_FILE" ]; then
  APPLE_ID="$(python3 -c "import json;print(json.load(open('$CONFIG_FILE')).get('appleId') or '')" 2>/dev/null || true)"
  if [ -n "$APPLE_ID" ]; then
    security delete-generic-password -a "$APPLE_ID" -s "local.informatiemanagement.tracker-contactsync" >/dev/null 2>&1 || true
    echo "App-specifiek wachtwoord uit de sleutelhanger verwijderd."
  fi
fi

rm -rf "$APP_SUPPORT"
echo "Instellingen en logbestanden verwijderd."
echo ""
echo "✅ Klaar. De groep en contacten in Contacten/iCloud blijven staan totdat je"
echo "   ze daar zelf verwijdert."
read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten..."
