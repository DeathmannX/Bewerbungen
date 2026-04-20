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
- `CORS` wurde auf explizite Origins gehärtet (konfigurierbar über `CORS_ALLOW_ORIGINS`), aber es gibt weiterhin keine Authentifizierung.
- Es gibt keine Authentifizierung/Autorisierung für API und PDF-Zugriff.
- Upload-Endpoint prüft inzwischen Dateiendung, MIME-Type und Größenlimit (`MAX_UPLOAD_SIZE_BYTES`).

### 2) Robustheit und Fehlerbehandlung
- DB-Migration nutzt ein breites `except: pass`; echte Fehler werden verschluckt.
- Frontend prüft Response-Status nur teilweise und behandelt API-Fehler inkonsistent.
- Bei PDF-Parsing-Fehlern wird nur geloggt; der API-Response bleibt heuristisch und unklar bezüglich Fehlerursache.

### 3) Datenqualität und Domänenregeln
- `dateApplied` und `statusDate` werden als Datum validiert.
- `status` ist als Enum (`offen`, `gemeldet`, `absage`) abgesichert.
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
- Request-Validierung (`422` bei unvollständiger Payload, ungültigem Status oder ungültigem Datum)
- Upload-Validierung (`415` bei falschem MIME-Type, `413` bei zu großer Datei)

### Pipeline lokal starten
```bash
bash scripts/test.sh
```

Das Skript:
1. nutzt den vorhandenen lokalen `venv`
2. installiert Dev-Abhängigkeiten aus `requirements-dev.txt`
3. führt `pytest` über `tests/` aus

## Nächste sinnvolle Ausbauschritte
1. Authentifizierung/Autorisierung ergänzen (z. B. Token oder Basic Auth im Intranet)
2. DB-Migration robuster machen (`except: pass` entfernen, Fehler gezielt behandeln)
3. Validierung weiter schärfen (z. B. Feldlängen, optionale Geschäftsregeln je Status)
4. Frontend modularisieren (Build-Schritt, Komponentenstruktur)
5. CI ergänzen, falls später gewünscht (z. B. GitHub Actions)


---
*Zuletzt synchronisiert via SSH am 20.04.2026*
