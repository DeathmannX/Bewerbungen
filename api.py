from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import os
import re
import uuid
import json
from datetime import datetime

# WICHTIG: pypdf muss installiert sein für die PDF Extraktion
from pypdf import PdfReader 

app = FastAPI(title="Bewerbungs-Manager API", description="Schnittstelle für den professionellen Bewerbungs-Manager")

# CORS aktivieren
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "bewerbungen.db"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Ordner für PDFs bereitstellen
app.mount("/pdfs", StaticFiles(directory=UPLOAD_DIR), name="pdfs")

# --- Datenbank Setup ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            company TEXT,
            jobTitle TEXT,
            dateApplied TEXT,
            platform TEXT,
            salary TEXT,
            hourlyWage REAL DEFAULT 0.0,
            isTempWork INTEGER DEFAULT 0,
            shifts TEXT, 
            status TEXT,
            statusDetail TEXT,
            statusDate TEXT,
            statusText TEXT,
            pdfPath TEXT,
            history TEXT
        )
    ''')
    # Migration: Spalten hinzufügen falls sie fehlen
    columns = [
        ("hourlyWage", "REAL DEFAULT 0.0"),
        ("isTempWork", "INTEGER DEFAULT 0"),
        ("history", "TEXT")
    ]
    for col_name, col_def in columns:
        try:
            c.execute(f"ALTER TABLE applications ADD COLUMN {col_name} {col_def}")
        except: pass
    
    conn.commit()
    conn.close()

init_db()

# --- Pydantic Models ---
class Shifts(BaseModel):
    morning: bool
    late: bool
    night: bool

class HistoryEntry(BaseModel):
    timestamp: str
    event: str

class Application(BaseModel):
    id: Optional[str] = None
    company: str
    jobTitle: str
    dateApplied: str
    platform: Optional[str] = ""
    salary: Optional[str] = ""
    hourlyWage: Optional[float] = 0.0
    isTempWork: Optional[bool] = False
    shifts: Shifts
    status: str
    statusDetail: Optional[str] = ""
    statusDate: Optional[str] = ""
    statusText: Optional[str] = ""
    pdfPath: Optional[str] = ""
    history: Optional[List[HistoryEntry]] = []

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        if col[0] == 'shifts':
            d[col[0]] = json.loads(row[idx])
        elif col[0] == 'isTempWork':
            d[col[0]] = bool(row[idx])
        elif col[0] == 'history':
            d[col[0]] = json.loads(row[idx]) if row[idx] else []
        else:
            d[col[0]] = row[idx]
    return d

# --- Frontend Auslieferung ---
@app.get("/")
async def read_index():
    return FileResponse('index.html')

# --- API Endpunkte ---

@app.get("/api/applications", response_model=List[Application])
def get_applications():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = dict_factory
    c = conn.cursor()
    c.execute("SELECT * FROM applications ORDER BY dateApplied DESC")
    apps = c.fetchall()
    conn.close()
    return apps

@app.post("/api/applications")
def create_application(app_data: Application):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    new_id = str(uuid.uuid4())
    shifts_json = json.dumps(app_data.shifts.dict())
    
    # Initiale History
    history = [{"timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"), "event": "Bewerbung erstellt"}]
    history_json = json.dumps(history)
    
    c.execute('''
        INSERT INTO applications 
        (id, company, jobTitle, dateApplied, platform, salary, hourlyWage, isTempWork, shifts, status, statusDetail, statusDate, statusText, pdfPath, history)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (new_id, app_data.company, app_data.jobTitle, app_data.dateApplied, app_data.platform, app_data.salary, 
          app_data.hourlyWage, 1 if app_data.isTempWork else 0,
          shifts_json, app_data.status, app_data.statusDetail, app_data.statusDate, app_data.statusText, app_data.pdfPath, history_json))
    conn.commit()
    conn.close()
    return {"message": "Gespeichert", "id": new_id}

@app.put("/api/applications/{app_id}")
def update_application(app_id: str, app_data: Application):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = dict_factory
    c = conn.cursor()
    
    # Alte Daten holen für Vergleich
    c.execute("SELECT * FROM applications WHERE id=?", (app_id,))
    old_app = c.fetchone()
    
    if not old_app:
        conn.close()
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    history = old_app.get('history', [])
    changes = []
    
    if old_app['status'] != app_data.status:
        changes.append(f"Status geändert: {old_app['status']} -> {app_data.status}")
    if old_app['company'] != app_data.company:
        changes.append(f"Firma geändert")
    if old_app['jobTitle'] != app_data.jobTitle:
        changes.append(f"Stelle geändert")
    if old_app['salary'] != app_data.salary:
        changes.append(f"Gehalt geändert")

    if changes:
        history.append({
            "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "event": "; ".join(changes)
        })

    shifts_json = json.dumps(app_data.shifts.dict())
    history_json = json.dumps(history)
    
    c.execute('''
        UPDATE applications SET 
        company=?, jobTitle=?, dateApplied=?, platform=?, salary=?, hourlyWage=?, isTempWork=?, shifts=?, 
        status=?, statusDetail=?, statusDate=?, statusText=?, pdfPath=?, history=?
        WHERE id=?
    ''', (app_data.company, app_data.jobTitle, app_data.dateApplied, app_data.platform, app_data.salary, 
          app_data.hourlyWage, 1 if app_data.isTempWork else 0,
          shifts_json, app_data.status, app_data.statusDetail, app_data.statusDate, app_data.statusText, app_data.pdfPath, history_json, app_id))
    conn.commit()
    conn.close()
    return {"message": "Aktualisiert"}

@app.delete("/api/applications/{app_id}")
def delete_application(app_id: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM applications WHERE id=?", (app_id,))
    conn.commit()
    conn.close()
    return {"message": "Gelöscht"}

@app.post("/api/extract")
async def extract_from_pdf(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)
        
    extracted_text = ""
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            extracted_text += page.extract_text() + "\n"
    except Exception as e:
        print(f"Fehler beim PDF lesen: {e}")

    company_guess = ""
    job_guess = ""
    
    # --- Präzise zeilenbasierte Extraktions-Logik ---
    lines = [l.strip() for l in extracted_text.split('\n') if l.strip()]

    for i, line in enumerate(lines):
        if re.search(r'(?:Bewerbung als|Arbeitsplatz als)', line, re.IGNORECASE):
            job_match = re.search(r'(?:Bewerbung als|Arbeitsplatz als)\s+(.*)', line, re.IGNORECASE)
            if job_match:
                job_guess = job_match.group(1).strip()
            
            if i + 1 < len(lines):
                next_line = lines[i+1]
                if re.search(r'\b(?:GmbH|AG|e\.V\.|OHG|KG)\b', next_line):
                    company_guess = next_line
            if not company_guess and i - 1 >= 0:
                prev_line = lines[i-1]
                if re.search(r'\b(?:GmbH|AG|e\.V\.|OHG|KG)\b', prev_line):
                    company_guess = prev_line
            if job_guess:
                break

    if not company_guess:
        for line in lines[5:]:
            if re.search(r'\b(?:GmbH|AG|e\.V\.|OHG|KG)\b', line):
                comp_match = re.search(r'(.*?\b(?:GmbH|AG|e\.V\.|OHG|KG)\b)', line)
                if comp_match:
                    company_guess = comp_match.group(1).strip()
                    break

    if company_guess:
        company_guess = re.sub(r'\d{5}\s+.*', '', company_guess).strip()

    if not job_guess:
        lines = [l.strip() for l in extracted_text.split('\n') if len(l.strip()) > 5]
        if lines: job_guess = lines[0]
    
    if not company_guess:
        company_guess = "Unbekannte Firma"

    return {
        "company": company_guess,
        "jobTitle": job_guess,
        "pdfPath": f"/pdfs/{filename}"
    }

@app.get("/api/ai-instructions")
def get_ai_instructions():
    return {
        "endpoint": "/api/applications",
        "method": "GET",
        "description": "Nutze diesen Endpunkt, um alle Bewerbungsdaten abzurufen.",
        "usage_for_llm": "Du bist ein Karriere-Assistent. Analysiere die Liste der Bewerbungen unter /api/applications."
    }
