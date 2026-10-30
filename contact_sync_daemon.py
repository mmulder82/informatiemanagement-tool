#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Contacten-sync achtergrondprogramma voor de Afdelingen-tracker.

Wat dit doet
------------
Houdt tracker-data.json in de gaten. Zodra het verandert, worden alle
contacten met een telefoonnummer als vCard naar een eigen, aparte groep in
iCloud-contacten gepusht (via CardDAV — hetzelfde protocol dat macOS/iOS
zelf gebruiken). Die groep synchroniseert vervolgens vanzelf door naar
Contacten op de Mac en de iPhone die aan hetzelfde iCloud-account hangen.

Veiligheid / scope
-------------------
- Dit script raakt UITSLUITEND vCard-resources aan waarvan de UID lokaal
  (in sync-state.json) is gegenereerd en onthouden. Er wordt nooit de hele
  adresboek-collectie opgehaald of doorzocht — dus bestaande contacten die
  niet door dit script zijn aangemaakt, worden nooit gelezen, gewijzigd of
  verwijderd.
- Het Apple-wachtwoord (app-specifiek wachtwoord) wordt uitsluitend in de
  macOS-sleutelhanger bewaard (via het ingebouwde `security`-commando) en
  nergens anders op schijf geschreven.
- De lokale HTTP-server luistert alleen op 127.0.0.1 (niet extern
  bereikbaar) en voert een verzoek alleen uit als het:
    1. de juiste koppelcode meestuurt (header X-Sync-Token; de code staat in
       config.json en wordt door de installer getoond) — zonder code kan een
       willekeurige website het programma dus niet aansturen of uitlezen;
    2. gericht is aan 127.0.0.1/localhost (Host-header; tegen DNS-rebinding);
    3. komt van een toegestane pagina: een lokaal bestand (file://),
       localhost/127.0.0.1, of een webadres uit "allowedOrigins" in config.json.
- Er komen geen contactgegevens in het logbestand of in sync-state.json
  (daar staat alleen een hash om wijzigingen te herkennen).

Geen enkele externe Python-package is vereist — alleen de standaardbibliotheek
plus het macOS-eigen `security`-commando voor de sleutelhanger.
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
import base64
import hashlib
import hmac
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPSConnection
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

APP_NAME = "InformatiemanagementTrackerSync"
KEYCHAIN_SERVICE = "local.informatiemanagement.tracker-contactsync"
CONFIG_DIR = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
STATE_FILE = os.path.join(CONFIG_DIR, "sync-state.json")
LOG_FILE = os.path.join(CONFIG_DIR, "sync.log")

DEFAULT_PORT = 8972
DEFAULT_GROUP_NAME = "Informatiemanagement Tracker"
POLL_INTERVAL_SEC = 5
TOKEN_HEADER = "X-Sync-Token"
MAX_BODY = 64 * 1024

DAV_NS = {"d": "DAV:", "cd": "urn:ietf:params:xml:ns:carddav"}

_lock = threading.RLock()

# Synchroniseren gebeurt op de achtergrond, zodat de tracker meteen antwoord krijgt
# (een volledige sync van tientallen contacten duurt langer dan de tracker wacht).
_sync_status_lock = threading.Lock()
_sync_loopt = False          # er draait nu een sync
_sync_nogmaals = False       # tijdens het draaien is er opnieuw om een sync gevraagd
_forceer_discovery = False   # bij de volgende sync het adresboek opnieuw opzoeken


# ---------- kleine helpers: config, state, logging ----------

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # eenvoudige rotatie: houd de laatste ~500 regels
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        if len(lines) > 500:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-500:])
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)


def get_config():
    return load_json(CONFIG_FILE, {})


def save_config(cfg):
    save_json(CONFIG_FILE, cfg)


def ensure_token():
    """Zorgt dat er een koppelcode in config.json staat. Normaal maakt de
    installer die aan en toont hem; dit is een vangnet."""
    with _lock:
        cfg = get_config()
        if not cfg.get("token"):
            cfg["token"] = secrets.token_urlsafe(24)
            save_config(cfg)
            log("Nieuwe koppelcode aangemaakt; draai de installer opnieuw om hem te zien.")
        return cfg["token"]


def get_state():
    return load_json(STATE_FILE, {"contacts": {}, "groupUid": None, "collectionHref": None,
                                   "host": None, "lastSync": None, "lastError": None,
                                   "contactCount": 0})


def save_state(st):
    save_json(STATE_FILE, st)


# ---------- macOS-sleutelhanger via het ingebouwde `security`-commando ----------

def keychain_set(account, password):
    subprocess.run(
        ["security", "add-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE,
         "-w", password, "-U"],
        check=True, capture_output=True, text=True,
    )


def keychain_get(account):
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
            check=True, capture_output=True, text=True,
        )
        return r.stdout.rstrip("\n")
    except Exception:
        return None


def keychain_delete(account):
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE],
            check=True, capture_output=True, text=True,
        )
    except Exception:
        pass


# ---------- CardDAV: minimale, doelgerichte client (alleen stdlib) ----------

class DavError(Exception):
    pass


def _basic_auth_header(apple_id, password):
    token = base64.b64encode(f"{apple_id}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def dav_request(host, method, path, apple_id, password, body=None, extra_headers=None, max_redirects=5):
    """Doet één DAV-request en volgt handmatig redirects (nodig voor PROPFIND,
    dat urllib niet automatisch herleidt). Geeft (host, status, headers, body_text) terug."""
    headers = {
        "Authorization": _basic_auth_header(apple_id, password),
        "User-Agent": "InformatiemanagementTrackerContactSync/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = body.encode("utf-8") if isinstance(body, str) else body

    for _ in range(max_redirects):
        conn = HTTPSConnection(host, timeout=20)
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read()
            status = resp.status
            resp_headers = dict(resp.getheaders())
        finally:
            conn.close()

        if status in (301, 302, 307, 308) and "Location" in resp_headers:
            loc = resp_headers["Location"]
            parts = urlsplit(loc)
            if parts.scheme and parts.netloc:
                host = parts.netloc
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
            else:
                path = loc
            continue

        return host, status, resp_headers, resp_body

    raise DavError(f"Te veel redirects voor {method} {path}")


def xml_find_text(root, path):
    el = root.find(path, DAV_NS)
    return el.text.strip() if el is not None and el.text else None


def discover_collection(apple_id, password):
    """Zoekt (host, addressbook_collection_path) op via de standaard CardDAV
    discovery-flow. Alleen leesoperaties (PROPFIND) — er wordt niets gewijzigd."""
    host = "contacts.icloud.com"

    body1 = (
        '<?xml version="1.0" encoding="utf-8" ?>'
        '<D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>'
    )
    host, status, _, resp = dav_request(
        host, "PROPFIND", "/", apple_id, password, body=body1,
        extra_headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
    )
    if status != 207:
        raise DavError(f"Kon principal niet ophalen (status {status}). Controleer Apple ID en app-specifiek wachtwoord.")
    root = ET.fromstring(resp)
    principal_href = xml_find_text(root, ".//d:current-user-principal/d:href")
    if not principal_href:
        raise DavError("Geen current-user-principal gevonden in iCloud-antwoord.")

    body2 = (
        '<?xml version="1.0" encoding="utf-8" ?>'
        '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
        '<D:prop><C:addressbook-home-set/></D:prop></D:propfind>'
    )
    host, status, _, resp = dav_request(
        host, "PROPFIND", principal_href, apple_id, password, body=body2,
        extra_headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
    )
    if status != 207:
        raise DavError(f"Kon addressbook-home-set niet ophalen (status {status}).")
    root = ET.fromstring(resp)
    home_href = xml_find_text(root, ".//cd:addressbook-home-set/d:href")
    if not home_href:
        raise DavError("Geen addressbook-home-set gevonden in iCloud-antwoord.")

    body3 = (
        '<?xml version="1.0" encoding="utf-8" ?>'
        '<D:propfind xmlns:D="DAV:">'
        '<D:prop><D:resourcetype/><D:displayname/></D:prop></D:propfind>'
    )
    host, status, _, resp = dav_request(
        host, "PROPFIND", home_href, apple_id, password, body=body3,
        extra_headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
    )
    if status != 207:
        raise DavError(f"Kon adresboeken niet ophalen (status {status}).")
    root = ET.fromstring(resp)
    collection_href = None
    for resp_el in root.findall(".//d:response", DAV_NS):
        href = xml_find_text(resp_el, "d:href")
        restype = resp_el.find(".//d:propstat/d:prop/d:resourcetype", DAV_NS)
        if restype is None or href is None:
            continue
        is_addressbook = restype.find("cd:addressbook", DAV_NS) is not None
        if is_addressbook and href.rstrip("/") != home_href.rstrip("/"):
            collection_href = href
            break
    if not collection_href:
        raise DavError("Geen adresboek-collectie gevonden onder je iCloud-account.")

    return host, collection_href


def dav_put_vcard(host, collection_href, uid, vcard_text, apple_id, password):
    path = collection_href.rstrip("/") + "/" + uid + ".vcf"
    host2, status, _, resp = dav_request(
        host, "PUT", path, apple_id, password, body=vcard_text,
        extra_headers={"Content-Type": "text/vcard; charset=utf-8"},
    )
    if status not in (200, 201, 204):
        log(f"PUT mislukt voor vCard {uid} (status {status}).")
        raise DavError(f"PUT van vCard {uid} mislukt (status {status}): {resp[:300]!r}")
    return host2


def dav_delete(host, collection_href, uid, apple_id, password):
    path = collection_href.rstrip("/") + "/" + uid + ".vcf"
    host2, status, _, resp = dav_request(host, "DELETE", path, apple_id, password)
    if status not in (200, 204, 404):
        raise DavError(f"Verwijderen mislukt (status {status}): {resp[:300]!r}")
    return host2


# ---------- vCard opbouwen ----------

def vcard_escape(s):
    return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


TUSSENVOEGSELS = {
    "van", "de", "der", "den", "ter", "ten", "het", "'t", "in", "te",
    "op", "aan", "onder", "over", "uit", "\u00e0", "el", "al",
}


def split_naam(naam):
    """Splitst een volledige naam in voornaam/achternaam, met een beetje oog voor
    Nederlandse tussenvoegsels (\'Jan de Vries\' -> \'Jan\' / \'de Vries\')."""
    words = naam.split()
    if len(words) <= 1:
        return "", naam
    for i in range(1, len(words)):
        if words[i].lower().strip(".,\'") in TUSSENVOEGSELS:
            return " ".join(words[:i]), " ".join(words[i:])
    return " ".join(words[:-1]), words[-1]


def build_contact_vcard(uid, naam, telefoon, email, rol):
    naam = (naam or "").strip() or "Naamloos"
    voornaam, achternaam = split_naam(naam)
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        "PRODID:-//Informatiemanagement Tracker//Contacten-sync 1.0//NL",
        f"UID:{uid}",
        f"FN:{vcard_escape(naam)}",
        f"N:{vcard_escape(achternaam)};{vcard_escape(voornaam)};;;",
    ]
    if telefoon:
        lines.append(f"TEL;TYPE=CELL:{vcard_escape(telefoon)}")
    if email:
        lines.append(f"EMAIL;TYPE=INTERNET:{vcard_escape(email)}")
    if rol:
        lines.append(f"TITLE:{vcard_escape(rol)}")
    lines.append("NOTE:Gesynchroniseerd vanuit de Informatiemanagement-tracker")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def build_group_vcard(uid, group_name, member_uids):
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        "PRODID:-//Informatiemanagement Tracker//Contacten-sync 1.0//NL",
        f"UID:{uid}",
        f"FN:{vcard_escape(group_name)}",
        f"N:{vcard_escape(group_name)};;;;",
        "X-ADDRESSBOOKSERVER-KIND:group",
    ]
    for m in member_uids:
        lines.append(f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{m}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ---------- tracker-data.json lezen ----------

def read_tracker_contacts(data_file):
    data = load_json(data_file, None)
    if data is None:
        raise DavError(f"Kan {data_file} niet lezen.")
    contacten = data.get("contacten") or []
    result = []
    for c in contacten:
        telefoon = (c.get("telefoon") or "").strip()
        if not telefoon:
            continue
        result.append({
            "id": c.get("id"),
            "naam": (c.get("naam") or "").strip(),
            "telefoon": telefoon,
            "email": (c.get("email") or "").strip(),
            "rol": (c.get("rol") or "").strip(),
        })
    return result


# ---------- de eigenlijke sync ----------

def sync_once():
    global _forceer_discovery
    with _lock:
        cfg = get_config()
        st = get_state()
        apple_id = cfg.get("appleId")
        data_file = cfg.get("dataFile")
        group_name = cfg.get("groupName") or DEFAULT_GROUP_NAME

        if not apple_id or not data_file:
            st["lastError"] = "Nog geen Apple ID / gegevensbestand ingesteld."
            save_state(st)
            return st

        password = keychain_get(apple_id)
        if not password:
            st["lastError"] = "Geen app-specifiek wachtwoord gevonden in de sleutelhanger."
            save_state(st)
            return st

        try:
            contacts = read_tracker_contacts(data_file)

            host = st.get("host")
            collection_href = st.get("collectionHref")
            if _forceer_discovery:
                host = collection_href = None
                _forceer_discovery = False
            if not host or not collection_href:
                host, collection_href = discover_collection(apple_id, password)
                st["host"] = host
                st["collectionHref"] = collection_href
                save_state(st)

            mapping = st.get("contacts") or {}
            current_ids = set()
            for c in contacts:
                tid = c["id"]
                if not tid:
                    continue
                current_ids.add(tid)
                entry = mapping.get(tid)
                cuid = entry["uid"] if entry else str(uuid.uuid4())
                fingerprint = hashlib.sha256(
                    f'{c["naam"]}|{c["telefoon"]}|{c["email"]}|{c["rol"]}'.encode("utf-8")
                ).hexdigest()
                if entry and entry.get("fingerprint") == fingerprint:
                    continue  # ongewijzigd, geen PUT nodig
                vcard = build_contact_vcard(cuid, c["naam"], c["telefoon"], c["email"], c["rol"])
                try:
                    host = dav_put_vcard(host, collection_href, cuid, vcard, apple_id, password)
                except DavError as e:
                    log(f"Fout bij opslaan contact (tracker-id {tid}): {e}")
                    continue
                mapping[tid] = {"uid": cuid, "fingerprint": fingerprint}

            removed = [tid for tid in list(mapping.keys()) if tid not in current_ids]
            for tid in removed:
                cuid = mapping[tid]["uid"]
                try:
                    host = dav_delete(host, collection_href, cuid, apple_id, password)
                except DavError as e:
                    log(f"Fout bij verwijderen contact-uid {cuid}: {e}")
                    continue
                del mapping[tid]

            group_uid = st.get("groupUid") or str(uuid.uuid4())
            member_uids = [m["uid"] for m in mapping.values()]
            group_vcard = build_group_vcard(group_uid, group_name, member_uids)
            try:
                host = dav_put_vcard(host, collection_href, group_uid, group_vcard, apple_id, password)
            except DavError as e:
                log(f"Fout bij opslaan van de groep {group_name!r}: {e}")
                st["lastError"] = f"Groep opslaan mislukt: {e}"
                save_state(st)
                return st

            st["host"] = host
            st["groupUid"] = group_uid
            st["contacts"] = mapping
            st["contactCount"] = len(mapping)
            st["lastSync"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            st["lastError"] = None
            save_state(st)
            log(f"Sync geslaagd: {len(mapping)} contact(en) in groep {group_name!r}.")
            return st
        except Exception as e:
            log("Sync mislukt:\n" + traceback.format_exc())
            st["lastError"] = str(e)
            save_state(st)
            return st


def _sync_achtergrond():
    global _sync_loopt, _sync_nogmaals
    while True:
        try:
            sync_once()
        except Exception:
            log("Sync mislukt:\n" + traceback.format_exc())
        with _sync_status_lock:
            if _sync_nogmaals:
                _sync_nogmaals = False
                continue
            _sync_loopt = False
            return


def start_sync_async():
    """Start een sync op de achtergrond. Loopt er al een, dan volgt er direct
    daarna nog één (zodat een wijziging van tijdens de sync ook meegaat)."""
    global _sync_loopt, _sync_nogmaals
    with _sync_status_lock:
        if _sync_loopt:
            _sync_nogmaals = True
            return
        _sync_loopt = True
    threading.Thread(target=_sync_achtergrond, daemon=True).start()


def sync_bezig():
    with _sync_status_lock:
        return _sync_loopt


def forceer_discovery():
    global _forceer_discovery
    _forceer_discovery = True


# ---------- achtergrond-polling: kijkt naar wijzigingen in tracker-data.json ----------

def poll_loop():
    last_mtime = None
    while True:
        try:
            cfg = get_config()
            data_file = cfg.get("dataFile")
            if data_file and os.path.exists(data_file):
                mtime = os.path.getmtime(data_file)
                if last_mtime is not None and mtime != last_mtime:
                    time.sleep(1.0)  # even wachten tot het schrijven echt klaar is
                    start_sync_async()
                last_mtime = mtime
        except Exception:
            log("Fout in poll-lus:\n" + traceback.format_exc())
        time.sleep(POLL_INTERVAL_SEC)


# ---------- lokale HTTP-server voor de instellingen in de tracker ----------

def allowed_web_origins():
    """Webadressen (bijv. https://naam.github.io) die de installer in config.json zette."""
    extra = get_config().get("allowedOrigins") or []
    return {o.strip().rstrip("/").lower() for o in extra if isinstance(o, str) and o.strip()}


def is_allowed_origin(origin):
    if not origin or origin == "null":
        return True  # file://-pagina's sturen Origin: null — de koppelcode beschermt
    try:
        parts = urlsplit(origin)
        if parts.hostname in ("localhost", "127.0.0.1"):
            return True
    except Exception:
        return False
    return origin.strip().rstrip("/").lower() in allowed_web_origins()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # geen ruis op stdout; we loggen zelf gerichter

    def _cors(self):
        # De Origin wordt teruggegeven zodat de tracker ook een foutmelding (geen
        # koppelcode, webadres niet toegestaan) kan lezen. Gegevens gaan pas mee
        # nadat _guard() Host, Origin én koppelcode heeft gecontroleerd.
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, " + TOKEN_HEADER)
        if self.headers.get("Access-Control-Request-Private-Network"):
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        """True als het verzoek mag; anders is er al een foutantwoord gestuurd."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._send_json({"error": "host"}, 421)
            return False
        origin = self.headers.get("Origin")
        if not is_allowed_origin(origin):
            self._send_json({"error": "origin", "origin": origin}, 403)
            return False
        token = get_config().get("token") or ""
        sent = self.headers.get(TOKEN_HEADER) or ""
        if not token or not hmac.compare_digest(sent.encode("utf-8"), token.encode("utf-8")):
            self._send_json({"error": "koppelcode"}, 401)
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._guard():
            return
        if self.path != "/status":
            self._send_json({"error": "not found"}, 404)
            return
        cfg = get_config()
        st = get_state()
        apple_id = cfg.get("appleId")
        self._send_json({
            "running": True,
            "appleId": apple_id,
            "groupName": cfg.get("groupName") or DEFAULT_GROUP_NAME,
            "hasCredentials": bool(apple_id and keychain_get(apple_id)),
            "lastSync": st.get("lastSync"),
            "lastError": st.get("lastError"),
            "contactCount": st.get("contactCount", 0),
            "syncing": sync_bezig(),
        })

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):
        if not self._guard():
            return
        if self.path == "/credentials":
            payload = self._read_json_body()
            apple_id = (payload.get("appleId") or "").strip()
            password = payload.get("password") or None
            group_name = (payload.get("groupName") or "").strip() or DEFAULT_GROUP_NAME
            if not apple_id:
                self._send_json({"error": "appleId ontbreekt"}, 400)
                return
            cfg = get_config()
            cfg["appleId"] = apple_id
            cfg["groupName"] = group_name
            save_config(cfg)
            if password:
                try:
                    keychain_set(apple_id, password)
                except Exception as e:
                    self._send_json({"error": f"Kon wachtwoord niet opslaan in sleutelhanger: {e}"}, 500)
                    return
            # Adresboek opnieuw opzoeken (het account kan gewijzigd zijn) en op de
            # achtergrond synchroniseren; de status laat zien wanneer het klaar is.
            forceer_discovery()
            start_sync_async()
            self._send_json({"ok": True, "syncing": True})
            return
        if self.path == "/sync-now":
            start_sync_async()
            self._send_json({"ok": True, "syncing": True})
            return
        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self._guard():
            return
        if self.path == "/credentials":
            cfg = get_config()
            apple_id = cfg.get("appleId")
            if apple_id:
                keychain_delete(apple_id)
            cfg.pop("appleId", None)
            save_config(cfg)
            self._send_json({"ok": True})
            return
        self._send_json({"error": "not found"}, 404)


def main():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except Exception:
        pass
    ensure_token()
    cfg = get_config()
    port = cfg.get("port") or DEFAULT_PORT
    log(f"Contacten-sync daemon start op poort {port}, databestand: {cfg.get('dataFile')}")

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
