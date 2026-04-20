# Bewerbungs-Manager

## Ziel und Zweck
Der Bewerbungs-Manager ist ein lokales Tool zur strukturierten Erfassung und Nachverfolgung von Bewerbungen. Er bündelt Bewerbungsdaten (Firma, Stelle, Status, Gehalt, Schichten, Verlauf), unterstützt den PDF-Import zur Vorbelegung und ermöglicht einen einfachen Export als Textdatei.

## Wie das System aktuell funktioniert

### Architektur
- Backend: `FastAPI` in [`api.py`](./api.py)
- Datenbank: `SQLite` in `bewerbungen.db`
- Frontend: Single-File React/Tailwind in [`index.html`](./index.html) (über CDN, ohne Build-Schritt)
- Uploads: PDF-Dateien im Ordner `uploads/`, bereitgestellt unter `/pdfs/{dateiname}`

### Datenfluss
1. Frontend lädt über `GET /api/applications` alle Bewerbungen.
2. Neue Einträge werden über `POST /api/applications` erstellt.
3. Änderungen laufen über `PUT /api/applications/{id}`.
4. Löschungen laufen über `DELETE /api/applications/{id}`.
5. PDF-Upload läuft über `POST /api/extract`:
   - Datei wird gespeichert
   - Text wird aus dem PDF extrahiert
   - Firma/Stelle werden heuristisch erkannt
   - `pdfPath` wird zur späteren Anzeige geliefert

### Kernfunktionen
- CRUD für Bewerbungen
- Historie bei Änderungen (Status/Firma/Stelle/Gehalt)
- Datums- und Statusfilter im Dashboard
- Gehaltsberechnung aus Stundenlohn (inkl. Zeitarbeit-Faktor)
- TXT-Export der gefilterten Einträge

## Styling-Bewertung

### Positiv
- Klarer, konsistenter Dark-Style mit monochromer Designlinie
- Gute visuelle Status-Codierung (Farbindikator + Badge)
- Übersichtliches Modal-Konzept für Erfassung und Verlauf
- Solide mobile Anpassung über responsive Utility-Klassen

### Einschränkungen
- Frontend liegt komplett in einer Datei (`index.html`), was Wartbarkeit und Skalierung begrenzt
- Tailwind/React/Babel kommen per CDN und Babel läuft im Browser (nicht optimal für Performance/Production-Hardening)
- Keine dedizierte Design-System-Struktur (Tokens/Komponenten getrennt versioniert)

## Schwachstellen (Ist-Stand)

### 1) Sicherheit
- `CORS` ist sehr offen (`allow_origins=["*"]` + `allow_credentials=True`), was für produktive Umgebungen riskant ist.
- Es gibt keine Authentifizierung/Autorisierung für API und PDF-Zugriff.
- Upload-Endpoint akzeptiert Dateien ohne serverseitige Dateityp-/Größenprüfung.

### 2) Robustheit und Fehlerbehandlung
- DB-Migration nutzt ein breites `except: pass`; echte Fehler werden verschluckt.
- Frontend prüft Response-Status nur teilweise und behandelt API-Fehler inkonsistent.
- Bei PDF-Parsing-Fehlern wird nur geloggt; der API-Response bleibt heuristisch und unklar bezüglich Fehlerursache.

### 3) Datenqualität und Domänenregeln
- Mehrere Felder (z. B. `dateApplied`, `status`) sind freie Strings ohne enge Validierung.
- Statuswerte werden implizit erwartet (`offen`, `gemeldet`, `absage`), aber nicht als Enum abgesichert.
- History-Einträge basieren auf einfacher Textlogik statt strukturierter Änderungsobjekte.

### 4) Betriebs- und Entwicklungsreife
- Kein Standard-`README.md` vorhanden gewesen.
- Vorher keine lauffähige Testpipeline (`pytest`/`httpx` fehlten).
- Kein CI-Setup (gewünscht ist lokal, daher in diesem Schritt nicht GitHub-basiert umgesetzt).

## Test-Pipeline (lokal)

### Enthaltene Tests
Die Datei [`tests/test_api.py`](./tests/test_api.py) prüft die Backend-Kernfälle:
- `GET /api/applications` (leer/gefüllt)
- `POST /api/applications` inkl. Initial-Historie
- `PUT /api/applications/{id}` inkl. Historienfortschreibung
- `PUT` auf unbekannte ID (`404`)
- `DELETE /api/applications/{id}`
- `POST /api/extract` mit gemockter PDF-Auslese
- Request-Validierung (`422` bei unvollständiger Payload)

### Pipeline lokal starten
```bash
bash scripts/test.sh
```

Das Skript:
1. nutzt den vorhandenen lokalen `venv`
2. installiert Dev-Abhängigkeiten aus `requirements-dev.txt`
3. führt `pytest` über `tests/` aus

## Nächste sinnvolle Ausbauschritte
1. Sicherheits-Härtung (Auth, CORS-Whitelist, Upload-Limits)
2. Striktere Validierung (Enums, Datumsvalidierung, Constraints)
3. Frontend modularisieren (Build-Schritt, Komponentenstruktur)
4. CI ergänzen, falls später gewünscht (z. B. GitHub Actions)


---
*Zuletzt synchronisiert via SSH am 20.04.2026*
