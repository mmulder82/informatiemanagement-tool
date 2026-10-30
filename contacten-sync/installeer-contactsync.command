#!/bin/bash
# Installeert het contacten-sync achtergrondprogramma voor de Afdelingen-tracker.
# Dubbelklik dit bestand in Finder om het te installeren (of voer het uit in Terminal).
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUESSED_DATA_FILE="$(cd "$SCRIPT_DIR/.." && pwd)/tracker-data.json"
APP_SUPPORT="$HOME/Library/Application Support/InformatiemanagementTrackerSync"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS/local.informatiemanagement.tracker-contactsync.plist"
LABEL="local.informatiemanagement.tracker-contactsync"
DAEMON_DEST="$APP_SUPPORT/contact_sync_daemon.py"
CONFIG_FILE="$APP_SUPPORT/config.json"

PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "⚠️  python3 is niet gevonden op dit systeem."
  echo "Installeer de Xcode Command Line Tools (voer 'xcode-select --install' uit in Terminal)"
  echo "en probeer daarna deze installatie opnieuw."
  read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten..."
  exit 1
fi

echo "== Contacten-sync installeren voor de Afdelingen-tracker =="
echo ""
echo "Let op: dit moet het tracker-data.json-bestand zijn waar de tracker ZELF"
echo "automatisch naar opslaat — dus de map die je in de tracker zelf hebt gekoppeld"
echo "via Instellingen → 'Map koppelen' (bijv. een OneDrive/SharePoint-map). Dat is"
echo "niet per se dezelfde map als waar dit installatieprogramma in staat."
echo ""

DATA_FILE=""
EXISTING_DATA_FILE=""
if [ -f "$CONFIG_FILE" ]; then
  EXISTING_DATA_FILE="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("dataFile") or "")' "$CONFIG_FILE" 2>/dev/null || true)"
fi

DEFAULT_GUESS="$EXISTING_DATA_FILE"
if [ -z "$DEFAULT_GUESS" ] && [ -f "$GUESSED_DATA_FILE" ]; then
  DEFAULT_GUESS="$GUESSED_DATA_FILE"
fi

if [ -n "$DEFAULT_GUESS" ]; then
  echo "Gevonden tracker-data.json: $DEFAULT_GUESS"
  read -r -p "Is dit het juiste bestand? (Enter = ja, of typ 'n' om zelf aan te wijzen): " ANTWOORD
  if [ -z "$ANTWOORD" ] || [ "$ANTWOORD" = "j" ] || [ "$ANTWOORD" = "J" ]; then
    DATA_FILE="$DEFAULT_GUESS"
  fi
fi

if [ -z "$DATA_FILE" ]; then
  echo ""
  echo "Sleep het juiste tracker-data.json-bestand (uit de map die de tracker zelf"
  echo "gebruikt om automatisch op te slaan) hierheen in dit venster, en druk op Enter:"
  read -r GESLEEPT
  # Terminal zet bij slepen een backslash voor spaties (of aanhalingstekens om het pad); haal die weg.
  DATA_FILE="$("$PYTHON_BIN" -c 'import shlex,sys
s = sys.argv[1].strip()
try:
    d = shlex.split(s)
except ValueError:
    d = [s]
print(d[0] if d else "")' "$GESLEEPT")"
fi

if [ ! -f "$DATA_FILE" ]; then
  echo ""
  echo "⚠️  Kan dit bestand niet vinden: $DATA_FILE"
  echo "Start de installatie opnieuw en sleep het juiste tracker-data.json-bestand aan."
  read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten..."
  exit 1
fi

if ! python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if 'afdelingen' in d else 1)" "$DATA_FILE" 2>/dev/null; then
  echo ""
  echo "⚠️  Dit bestand lijkt geen geldig tracker-data.json van de Afdelingen-tracker te zijn"
  echo "    (verwacht JSON met daarin onder meer een 'afdelingen'-sleutel)."
  read -r -p "Toch doorgaan met dit bestand? (j/n): " DOORGAAN
  if [ "$DOORGAAN" != "j" ] && [ "$DOORGAAN" != "J" ]; then
    echo "Installatie afgebroken."
    read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten..."
    exit 1
  fi
fi

echo ""
echo "Gebruik dit bestand: $DATA_FILE"
echo ""

echo "python3 gevonden: $PYTHON_BIN"

mkdir -p "$APP_SUPPORT"
chmod 700 "$APP_SUPPORT"
mkdir -p "$LAUNCH_AGENTS"
cp "$SCRIPT_DIR/contact_sync_daemon.py" "$DAEMON_DEST"
echo "Achtergrondprogramma gekopieerd naar: $DAEMON_DEST"

echo ""
EXISTING_ORIGINS="$("$PYTHON_BIN" -c 'import json,sys
try:
    print(", ".join(json.load(open(sys.argv[1])).get("allowedOrigins") or []))
except Exception:
    print("")' "$CONFIG_FILE" 2>/dev/null || true)"
echo "Open je de tracker via een webadres, bijvoorbeeld https://jouwnaam.github.io?"
echo "Vul dat adres dan in. Open je hem als bestand vanaf je computer, druk dan op Enter."
if [ -n "$EXISTING_ORIGINS" ]; then
  echo "(Al toegestaan: $EXISTING_ORIGINS)"
fi
read -r -p "Webadres: " WEBADRES

# Instellingen schrijven (bestaande Apple ID, groepsnaam en koppelcode blijven staan).
"$PYTHON_BIN" - "$CONFIG_FILE" "$DATA_FILE" "$WEBADRES" <<'PYEOF'
import json, os, secrets, sys
from urllib.parse import urlsplit
path, data_file, webadres = sys.argv[1], sys.argv[2], sys.argv[3].strip()
cfg = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
cfg["dataFile"] = data_file
cfg.setdefault("port", 8972)
cfg.setdefault("groupName", "Informatiemanagement Tracker")
if not cfg.get("token"):
    cfg["token"] = secrets.token_urlsafe(24)
origins = [o for o in (cfg.get("allowedOrigins") or []) if isinstance(o, str) and o.strip()]
if webadres:
    if "://" not in webadres:
        webadres = "https://" + webadres
    p = urlsplit(webadres)
    if p.scheme in ("http", "https") and p.netloc:
        origin = f"{p.scheme}://{p.netloc}".lower()
        if origin not in origins:
            origins.append(origin)
        print(f"Webadres toegestaan: {origin}")
    else:
        print(f"Let op: '{webadres}' is geen geldig webadres en is overgeslagen.")
cfg["allowedOrigins"] = origins
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print(f"Instellingen opgeslagen in: {path}")
PYEOF
TOKEN="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$CONFIG_FILE")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$DAEMON_DEST</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$APP_SUPPORT/daemon-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$APP_SUPPORT/daemon-stderr.log</string>
</dict>
</plist>
EOF
echo "LaunchAgent geschreven: $PLIST"

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl enable "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true

echo ""
echo "✅ Geïnstalleerd en (opnieuw) gestart, met als databestand:"
echo "   $DATA_FILE"
echo ""
KLEMBORD=""
if printf "%s" "$TOKEN" | pbcopy 2>/dev/null; then KLEMBORD=" (staat ook op je klembord)"; fi
echo "Koppelcode$KLEMBORD:"
echo ""
echo "   $TOKEN"
echo ""
echo "Open de tracker, ga naar Instellingen → 'Contacten synchroniseren met iPhone/Mac'"
echo "en plak deze code bij 'Koppelcode'. Vul daarna je Apple ID en app-specifiek wachtwoord"
echo "in, of klik op 'Nu synchroniseren' als die al waren ingesteld."
echo "Code kwijt? Draai dit installatieprogramma opnieuw; de code blijft dezelfde."
echo ""
read -n 1 -s -r -p "Druk op een toets om dit venster te sluiten..."
