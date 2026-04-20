#!/bin/bash
# Dieses Skript richtet den Bewerbungs-Manager als Hintergrunddienst auf Ubuntu ein.

APP_DIR=$(pwd)
PORT=8000 # Hier kannst du deinen frei wählbaren Port eintragen

echo "Starte Installation des professionellen Bewerbungs-Managers..."
echo "Installationsverzeichnis: $APP_DIR"

# 1. System aktualisieren & Python installieren (falls nicht vorhanden)
sudo apt update
sudo apt install -y python3 python3-pip python3-venv

# 2. Python Virtual Environment erstellen und Pakete installieren
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install fastapi uvicorn pydantic pypdf python-multipart

# 3. Systemd Service Datei erstellen
SERVICE_FILE="/etc/systemd/system/bewerbungsmanager.service"

sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=Bewerbungs-Manager API & Backend
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn api:app --host 0.0.0.0 --port $PORT
Restart=always
Environment=PYTHONPATH=$APP_DIR

[Install]
WantedBy=multi-user.target
EOF

# 4. Dienst aktivieren und starten
sudo systemctl daemon-reload
sudo systemctl enable bewerbungsmanager
sudo systemctl restart bewerbungsmanager

echo "=========================================================="
echo "Installation abgeschlossen!"
echo "Der Bewerbungs-Manager läuft nun im Hintergrund auf Port $PORT."
echo "URL: http://localhost:$PORT"
echo "Status prüfen mit: sudo systemctl status bewerbungsmanager"
echo "Logs ansehen mit: sudo journalctl -u bewerbungsmanager -f"
echo "=========================================================="
