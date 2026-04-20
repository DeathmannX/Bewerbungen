# Integration mit LM Studio / Lokaler KI

Dieser Bewerbungs-Manager bietet eine einfache Schnittstelle für deine lokale KI, um auf deine Bewerbungsdaten zuzugreifen.

## 1. Daten abrufen
Die KI kann alle deine Bewerbungen im JSON-Format über folgenden Endpunkt abrufen:
`GET http://localhost:8000/api/applications`

## 2. LM Studio Konfiguration
Wenn du LM Studio nutzt, kannst du der KI (z.B. im System Prompt) folgendes mitteilen:

> "Du hast Zugriff auf meinen Bewerbungs-Manager. Rufe die aktuellen Daten von `http://localhost:8000/api/applications` ab (falls dein Tool-Einsatz das unterstützt) oder nutze die bereitgestellten JSON-Daten, um mir beim Schreiben von Anschreiben oder bei der Analyse meiner Suche zu helfen."

## 3. PDF Zugriff
Die hochgeladenen Bewerbungs-PDFs sind direkt über den Browser oder die KI unter folgender URL erreichbar:
`http://localhost:8000/pdfs/{dateiname}`

Der Dateiname wird in der JSON-Antwort von `/api/applications` im Feld `pdfPath` mitgeliefert.

## 4. Beispiel für einen Prompt in LM Studio
"Schau dir meine letzten 3 Bewerbungen an. Welche Branchen habe ich bisher abgedeckt und wo sollte ich mich als nächstes bewerben?"
