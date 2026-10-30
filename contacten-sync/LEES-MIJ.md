# Contacten synchroniseren met iPhone/Mac

Synchroniseert automatisch alle contacten mét telefoonnummer uit de
Afdelingen-tracker naar een aparte groep in Contacten op je Mac en iPhone
(via iCloud). Draait volledig lokaal: er komt geen server aan te pas en je
wachtwoord verlaat je Mac nooit.

## Installeren

1. Download deze map (`contacten-sync`) naar je Mac, bij voorkeur in dezelfde
   map als je `tracker-data.json`.
2. Dubbelklik `installeer-contactsync.command`. Sta het openen toe als macOS
   waarschuwt voor een onbekende ontwikkelaar (rechtsklik → Openen, of via
   Systeeminstellingen → Privacy & beveiliging). Het installatieprogramma vraagt
   naar het juiste `tracker-data.json`-bestand — dat is het bestand waar de
   tracker zelf automatisch naar opslaat (te zien via Instellingen → 'Map
   koppelen' in de tracker). Klopt het voorgestelde pad niet, sleep dan het
   juiste bestand in het Terminal-venster.
   Daarna vraagt het of je de tracker via een **webadres** opent (bijv.
   `https://jouwnaam.github.io`). Vul dat in, of druk op Enter als je de
   tracker als bestand vanaf je computer opent. Aan het eind toont het een
   **koppelcode** en zet die op je klembord.
3. Open de tracker, ga naar **Instellingen → Contacten
   synchroniseren met iPhone/Mac**, plak de **koppelcode** en vul daarna in:
   - Je Apple ID (het account dat ook op je iPhone staat)
   - Een **app-specifiek wachtwoord** — aanmaken via appleid.apple.com →
     Aanmelden en beveiliging → App-specifieke wachtwoorden
   - De naam van de groep die in Contacten moet verschijnen
4. Klik op Opslaan. Vanaf nu wordt er automatisch gesynchroniseerd zodra er
   iets wijzigt in de tracker.

## Hoe het werkt

- Een klein achtergrondprogramma (`contact_sync_daemon.py`) start automatisch
  bij het inloggen (via een macOS LaunchAgent) en luistert alleen lokaal op
  poort 8972.
- Het houdt `tracker-data.json` in de gaten; bij een wijziging worden de
  contacten met telefoonnummer als vCard naar iCloud gepusht via CardDAV.
- Het voert alleen opdrachten uit die de juiste koppelcode meesturen, en
  alleen van een tracker die als bestand op je computer, via localhost of via
  het webadres uit de installatie is geopend. Andere websites kunnen het
  programma dus niet aansturen of uitlezen. De koppelcode staat in
  `config.json` en in de browser waarin je hem hebt geplakt.
- Het wachtwoord staat alleen in de macOS-sleutelhanger, nooit in
  `tracker-data.json` of in de tracker zelf.
- Er komen geen contactgegevens in het logbestand.
- Werkt in Chrome en Edge (de browsers waarin de tracker een map kan koppelen).
- Het script raakt uitsluitend de contacten aan die het zelf heeft
  aangemaakt (bijgehouden in een lokaal bestand) — je overige, bestaande
  contacten in iCloud worden nooit gelezen of gewijzigd.
- De tracker is leidend: een wijziging die je rechtstreeks in Contacten
  maakt op een gesynchroniseerd contact, kan bij de volgende sync worden
  overschreven.

## Verwijderen

Dubbelklik `verwijder-contactsync.command`. Dit stopt de achtergronddienst en
verwijdert het bewaarde wachtwoord; de groep en contacten blijven gewoon
staan in Contacten totdat je ze daar zelf verwijdert.

## Problemen oplossen

- Status en eventuele foutmelding staan ook in de tracker zelf, bij
  Instellingen → Contacten synchroniseren met iPhone/Mac.
- De tracker vraagt om een koppelcode, of zegt dat de code niet klopt? Draai
  de installer opnieuw: hij toont de code weer en zet hem op je klembord.
- De tracker meldt dat het webadres niet is toegestaan? Draai de installer
  opnieuw en vul bij de vraag naar het webadres het adres in dat de tracker
  noemt.
- 0 gesynchroniseerde contacten terwijl je die wel hebt? Dan wijst de daemon
  waarschijnlijk naar het verkeerde tracker-data.json-bestand — draai de
  installer opnieuw en wijs het juiste bestand aan (zie hierboven).
- Een logbestand staat in:
  `~/Library/Application Support/InformatiemanagementTrackerSync/sync.log`
- Het huidige databestand staat ook in:
  `~/Library/Application Support/InformatiemanagementTrackerSync/config.json`
