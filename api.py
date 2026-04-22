from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import io
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --- .env Loader ---
def load_dotenv():
    env_path = Path(".env")
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    # Extrem wichtig: Anführungszeichen und Leerzeichen entfernen
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")
    
    # Debug-Info
    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    print(f"--- KONFIGURATION GEPRÜFT ---")
    print(f"Primäres Modell: {model}")
    print(f"API-Key geladen: {'JA' if api_key else 'NEIN'} (Länge: {len(api_key)})")
    print(f"-----------------------------")

load_dotenv()

# WICHTIG: pypdf muss installiert sein für die PDF Extraktion
from pypdf import PdfReader

app = FastAPI(title="Bewerbungs-Manager API", description="Schnittstelle für den professionellen Bewerbungs-Manager")

# CORS sicherer konfigurieren
DEFAULT_CORS_ALLOW_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

raw_cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "")
if raw_cors_origins.strip():
    CORS_ALLOW_ORIGINS = [origin.strip() for origin in raw_cors_origins.split(",") if origin.strip()]
else:
    CORS_ALLOW_ORIGINS = DEFAULT_CORS_ALLOW_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    from fastapi import HTTPException as FastAPIHTTPException
    if isinstance(exc, FastAPIHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    import traceback
    print(f"GLOBAL ERROR: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": f"Interner Serverfehler: {str(exc)}", "traceback": traceback.format_exc()},
    )

from fastapi.responses import JSONResponse

DB_FILE = "bewerbungen.db"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(5 * 1024 * 1024)))
ALLOWED_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
ALLOWED_TEXT_CONTENT_TYPES = {"text/plain", "application/octet-stream", ""}

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
COVER_LETTER_TARGET_SCORE = float(os.getenv("COVER_LETTER_TARGET_SCORE", "9"))
COVER_LETTER_MAX_ROUNDS = int(os.getenv("COVER_LETTER_MAX_ROUNDS", "6"))

# Ordner für PDFs bereitstellen
app.mount("/pdfs", StaticFiles(directory=UPLOAD_DIR), name="pdfs")


# --- Datenbank Setup ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        '''
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
        '''
    )

    # Migration: Spalten hinzufügen falls sie fehlen
    columns = [
        ("hourlyWage", "REAL DEFAULT 0.0"),
        ("isTempWork", "INTEGER DEFAULT 0"),
        ("history", "TEXT"),
    ]
    for col_name, col_def in columns:
        try:
            c.execute(f"ALTER TABLE applications ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS cover_letter_projects (
            id TEXT PRIMARY KEY,
            createdAt TEXT NOT NULL,
            updatedAt TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            targetScore REAL NOT NULL DEFAULT 9.0,
            maxRounds INTEGER NOT NULL DEFAULT 6,
            latestDraft TEXT DEFAULT '',
            latestScore REAL DEFAULT 0.0,
            latestFeedback TEXT DEFAULT ''
        )
        '''
    )

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS cover_letter_sources (
            projectId TEXT PRIMARY KEY,
            jobSourceType TEXT DEFAULT '',
            jobSourceValue TEXT DEFAULT '',
            jobAnalysisJson TEXT DEFAULT '',
            baselineLetter TEXT DEFAULT '',
            resumeText TEXT DEFAULT '',
            resumeSourceType TEXT DEFAULT '',
            resumeSummaryJson TEXT DEFAULT '',
            FOREIGN KEY(projectId) REFERENCES cover_letter_projects(id)
        )
        '''
    )

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS cover_letter_resume_context_entries (
            id TEXT PRIMARY KEY,
            projectId TEXT NOT NULL,
            sortOrder INTEGER NOT NULL DEFAULT 0,
            company TEXT DEFAULT '',
            role TEXT DEFAULT '',
            tasks TEXT DEFAULT '',
            experiences TEXT DEFAULT '',
            liked TEXT DEFAULT '',
            disliked TEXT DEFAULT '',
            atmosphere TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            FOREIGN KEY(projectId) REFERENCES cover_letter_projects(id)
        )
        '''
    )

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS cover_letter_iterations (
            id TEXT PRIMARY KEY,
            projectId TEXT NOT NULL,
            roundIndex INTEGER NOT NULL,
            draftText TEXT NOT NULL,
            scoreTotal REAL NOT NULL,
            scoreBreakdownJson TEXT NOT NULL,
            rationale TEXT DEFAULT '',
            improvementHintsJson TEXT DEFAULT '',
            feedbackUsed TEXT DEFAULT '',
            createdAt TEXT NOT NULL,
            FOREIGN KEY(projectId) REFERENCES cover_letter_projects(id)
        )
        '''
    )

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS cover_letter_exports (
            id TEXT PRIMARY KEY,
            projectId TEXT NOT NULL,
            exportType TEXT NOT NULL,
            content TEXT NOT NULL,
            createdAt TEXT NOT NULL,
            FOREIGN KEY(projectId) REFERENCES cover_letter_projects(id)
        )
        '''
    )

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS applicant_profiles (
            id TEXT PRIMARY KEY,
            profileName TEXT NOT NULL,
            resumeText TEXT DEFAULT '',
            resumeSummaryJson TEXT DEFAULT '',
            baselineLetter TEXT DEFAULT '',
            updatedAt TEXT NOT NULL
        )
        '''
    )

    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS applicant_profile_context_entries (
            id TEXT PRIMARY KEY,
            profileId TEXT NOT NULL,
            sortOrder INTEGER NOT NULL DEFAULT 0,
            company TEXT DEFAULT '',
            role TEXT DEFAULT '',
            tasks TEXT DEFAULT '',
            experiences TEXT DEFAULT '',
            liked TEXT DEFAULT '',
            disliked TEXT DEFAULT '',
            atmosphere TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            FOREIGN KEY(profileId) REFERENCES applicant_profiles(id)
        )
        '''
    )

    conn.commit()
    conn.close()


init_db()


# --- Pydantic Models (bestehend) ---
class Shifts(BaseModel):
    morning: bool
    late: bool
    night: bool


class HistoryEntry(BaseModel):
    timestamp: str
    event: str


class ApplicationStatus(str, Enum):
    offen = "offen"
    gemeldet = "gemeldet"
    absage = "absage"


class Application(BaseModel):
    id: Optional[str] = None
    company: str
    jobTitle: str
    dateApplied: date
    platform: Optional[str] = ""
    salary: Optional[str] = ""
    hourlyWage: Optional[float] = 0.0
    isTempWork: Optional[bool] = False
    shifts: Shifts
    status: ApplicationStatus
    statusDetail: Optional[str] = ""
    statusDate: Optional[date] = None
    statusText: Optional[str] = ""
    pdfPath: Optional[str] = ""
    history: List[HistoryEntry] = Field(default_factory=list)

    @field_validator("statusDate", mode="before")
    @classmethod
    def empty_string_status_date_is_none(cls, value):
        if value in ("", None):
            return None
        return value


# --- Pydantic Models (Cover Letter) ---
class CoverLetterProjectCreate(BaseModel):
    targetScore: Optional[float] = COVER_LETTER_TARGET_SCORE
    maxRounds: Optional[int] = COVER_LETTER_MAX_ROUNDS

    @field_validator("targetScore")
    @classmethod
    def validate_target_score(cls, value):
        if value is None:
            return COVER_LETTER_TARGET_SCORE
        if value < 1 or value > 10:
            raise ValueError("targetScore muss zwischen 1 und 10 liegen")
        return value

    @field_validator("maxRounds")
    @classmethod
    def validate_max_rounds(cls, value):
        if value is None:
            return COVER_LETTER_MAX_ROUNDS
        if value < 1 or value > 20:
            raise ValueError("maxRounds muss zwischen 1 und 20 liegen")
        return value


class CoverLetterJobSourceInput(BaseModel):
    sourceType: str
    sourceValue: str
    contactPerson: Optional[str] = ""

    @field_validator("sourceType")
    @classmethod
    def validate_source_type(cls, value):
        clean = (value or "").strip().lower()
        if clean not in {"link", "text"}:
            raise ValueError("sourceType muss 'link' oder 'text' sein")
        return clean

    @field_validator("sourceValue")
    @classmethod
    def validate_source_value(cls, value):
        clean = (value or "").strip()
        if not clean:
            raise ValueError("sourceValue darf nicht leer sein")
        return clean


class CoverLetterAnalyzeLinkInput(BaseModel):
    url: Optional[str] = None


class CoverLetterResumeContextEntryInput(BaseModel):
    company: Optional[str] = ""
    role: Optional[str] = ""
    tasks: Optional[str] = ""
    experiences: Optional[str] = ""
    liked: Optional[str] = ""
    disliked: Optional[str] = ""
    atmosphere: Optional[str] = ""
    notes: Optional[str] = ""


class CoverLetterResumeContextInput(BaseModel):
    entries: List[CoverLetterResumeContextEntryInput] = Field(default_factory=list)


class CoverLetterGenerateInput(BaseModel):
    targetScore: Optional[float] = None
    maxRounds: Optional[int] = None


class CoverLetterFeedbackInput(BaseModel):
    feedback: str

    @field_validator("feedback")
    @classmethod
    def validate_feedback(cls, value):
        clean = (value or "").strip()
        if not clean:
            raise ValueError("feedback darf nicht leer sein")
        return clean


class CoverLetterIterateInput(BaseModel):
    targetScore: Optional[float] = None
    maxRounds: Optional[int] = None
    feedback: Optional[str] = None


class ApplicantProfileContextEntryInput(BaseModel):
    company: Optional[str] = ""
    role: Optional[str] = ""
    tasks: Optional[str] = ""
    experiences: Optional[str] = ""
    liked: Optional[str] = ""
    disliked: Optional[str] = ""
    atmosphere: Optional[str] = ""
    notes: Optional[str] = ""


class ApplicantProfileInput(BaseModel):
    id: Optional[str] = None
    profileName: str
    resumeText: Optional[str] = ""
    baselineLetter: Optional[str] = ""
    contextEntries: List[ApplicantProfileContextEntryInput] = Field(default_factory=list)

    @field_validator("profileName")
    @classmethod
    def validate_profile_name(cls, value):
        clean = (value or "").strip()
        if not clean:
            raise ValueError("Profilname darf nicht leer sein")
        return clean


# --- Hilfsfunktionen ---
def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        if col[0] == "shifts":
            d[col[0]] = json.loads(row[idx])
        elif col[0] == "isTempWork":
            d[col[0]] = bool(row[idx])
        elif col[0] == "history":
            d[col[0]] = json.loads(row[idx]) if row[idx] else []
        elif col[0] == "statusDate":
            d[col[0]] = row[idx] if row[idx] else None
        else:
            d[col[0]] = row[idx]
    return d


def sanitize_filename(filename: str) -> str:
    base_name = Path(filename).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base_name)
    return cleaned or "upload.pdf"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sqlite_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def safe_json_loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def clamp_score(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = 0.0
    return max(1.0, min(10.0, round(num, 2)))


def require_project(conn: sqlite3.Connection, project_id: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM cover_letter_projects WHERE id=?", (project_id,))
    row = cur.fetchone()
    project = sqlite_row_to_dict(row)
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return project


def ensure_project_sources_row(conn: sqlite3.Connection, project_id: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT projectId FROM cover_letter_sources WHERE projectId=?", (project_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO cover_letter_sources (projectId) VALUES (?)", (project_id,))


def normalize_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL fehlt")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Ungültige URL")
    return url


def fetch_url_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) BewerbungsManager/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = response.read(900_000)
            charset = response.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="ignore")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Fehler beim Laden der URL: {exc}")


def html_to_text(html: str) -> Dict[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = unescape(title_match.group(1).strip()) if title_match else ""

    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?(p|div|li|h1|h2|h3|h4|h5|h6|br|tr|td|section|article)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    plain = "\n".join(lines)
    return {"title": title, "text": plain}


def extract_company_candidate(text: str) -> str:
    match = re.search(
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.,\- ]{2,80}\b(?:GmbH\s*&\s*Co\.\s*KG|GmbH|AG|KG|OHG|e\.V\.))",
        text,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def extract_job_title_candidate(text: str) -> str:
    patterns = [
        r"(?:Bewerbung\s+als|Arbeitsplatz\s+als|Position\s+als|Stelle\s+als)\s+([^\n\r]{3,120})",
        r"Wir\s+suchen\s+(?:eine?n?\s+)?([^\n\r]{3,120})",
        r"Jobtitel[:\s]+([^\n\r]{3,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .:-")
            if len(candidate) >= 3:
                return candidate
    return ""


def extract_requirements(text: str) -> List[str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    req = []
    for line in lines:
        lower = line.lower()
        if any(k in lower for k in ["anforder", "profil", "qualifikation", "du bringst", "ihr profil", "voraussetzung"]):
            req.append(line)
        if len(req) >= 8:
            break
    return req


def extract_customers(text: str) -> str:
    for line in text.split("\n"):
        lower = line.lower()
        if "kunde" in lower or "kunden" in lower:
            return line[:220]
    return ""


def analyze_job_text(source_text: str, source_url: str = "", page_title: str = "") -> Dict[str, Any]:
    company = extract_company_candidate(source_text[:6000])
    job_title = extract_job_title_candidate(source_text[:6000]) or page_title
    requirements = extract_requirements(source_text)
    summary_line = ""

    for line in source_text.split("\n"):
        lower = line.lower()
        if any(k in lower for k in ["wir sind", "unternehmen", "weltweit", "hersteller", "dienstleister", "gruppe"]):
            summary_line = line[:240]
            break

    lower_text = source_text.lower()
    is_temp = any(k in lower_text for k in ["zeitarbeit", "arbeitnehmerüberlassung", "personaldienstleister"])

    result = {
        "sourceUrl": source_url,
        "pageTitle": page_title,
        "companyName": company or "Unbekannt",
        "jobTitle": job_title.strip() if job_title else "Unbekannt",
        "requirements": requirements,
        "companySummary": summary_line,
        "customers": extract_customers(source_text),
        "isTempWork": is_temp,
        "contactPerson": "", # Neues Feld
    }

    # Falls Gemini verfügbar: strukturierte Nachschärfung auf Basis des gelesenen Textes.
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        prompt = f"""
Analysiere die folgende Stellenanzeige und gib STRICT JSON zurück.
Keine Markdown-Formatierung, nur ein JSON-Objekt mit exakt diesen Schlüsseln:
companyName (string), jobTitle (string), requirements (array of strings), companySummary (string), customers (string), isTempWork (boolean), contactPerson (string).

WICHTIG für contactPerson: 
Suche nach einem konkreten Ansprechpartner (z.B. "Frau Müller" oder "Herr Schmidt"). 
Wenn kein Name gefunden wird, lass das Feld leer ("").

URL: {source_url or 'n/a'}
Titel: {page_title or 'n/a'}

Textauszug:
{source_text[:12000]}
""".strip()

        try:
            llm = call_gemini_json(prompt)
            result["companyName"] = (llm.get("companyName") or result["companyName"]).strip()
            result["jobTitle"] = (llm.get("jobTitle") or result["jobTitle"]).strip()
            llm_requirements = llm.get("requirements")
            if isinstance(llm_requirements, list):
                result["requirements"] = [str(x).strip() for x in llm_requirements if str(x).strip()][:12]
            result["companySummary"] = str(llm.get("companySummary") or result["companySummary"]).strip()
            result["customers"] = str(llm.get("customers") or result["customers"]).strip()
            if isinstance(llm.get("isTempWork"), bool):
                result["isTempWork"] = llm["isTempWork"]
            result["contactPerson"] = str(llm.get("contactPerson") or "").strip()
        except Exception:
            pass

    return result


def parse_resume_summary(resume_text: str) -> Dict[str, Any]:
    lines = [line.strip() for line in resume_text.split("\n") if line.strip()]

    stations = []
    for line in lines:
        if re.search(r"\b(19|20)\d{2}\b", line):
            stations.append(line)
        if len(stations) >= 12:
            break

    skill_keywords = [
        "industriemechanik", "zerspanung", "cnc", "wartung", "instandhaltung",
        "hydraulik", "pneumatik", "montage", "qualität", "produktion",
        "schweißen", "drehen", "fräsen", "schicht", "team"
    ]
    lower = resume_text.lower()
    found_skills = [kw for kw in skill_keywords if kw in lower]

    return {
        "characterCount": len(resume_text),
        "stationHints": stations,
        "skills": found_skills[:20],
        "preview": "\n".join(lines[:20]),
    }


def extract_text_from_pdf_bytes(content: bytes) -> str:
    text = ""
    reader = PdfReader(io.BytesIO(content))
    for page in reader.pages:
        text += (page.extract_text() or "") + "\n"
    return text.strip()


def extract_json_from_text(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Leere KI-Antwort")

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    obj = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if obj:
        parsed = json.loads(obj.group(1))
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Kein JSON-Objekt in KI-Antwort gefunden")


def call_gemini_text(prompt: str, temperature: float = 0.25) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY ist nicht gesetzt")

    # Liste der zu testenden Modelle (Bestes -> Fallback)
    preferred_model = os.getenv("GEMINI_MODEL", "").strip()
    models_to_try = []
    if preferred_model:
        models_to_try.append(preferred_model)
    
    # Standard-Kaskade (Priorisierung von 1.5-flash für maximale Kompatibilität im Free-Tier)
    for m in ["gemini-1.5-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.0-pro"]:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error = "Keine Modelle zum Testen vorhanden"
    saw_temporary_upstream_issue = False

    for model_name in models_to_try:
        # Wir probieren erst v1beta, dann v1 (Fallback-URLs)
        for api_version in ["v1beta", "v1"]:
            url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model_name}:generateContent?key={api_key}"
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "topP": 0.95,
                    "maxOutputTokens": 8192,
                },
            }

            try:
                req = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(req, timeout=90) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))

                    # Extrahiere Text (robust)
                    candidates = payload.get("candidates") or []
                    if candidates:
                        content = candidates[0].get("content") or {}
                        parts = content.get("parts") or []
                        if parts:
                            text = parts[0].get("text", "")
                            if text.strip():
                                print(f"--- KI ERFOLG --- Modell: {model_name} ({api_version})")
                                return text
            except HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="ignore")
                last_error = f"Modell {model_name} ({api_version}) Fehler {exc.code}: {err_body}"
                print(f"DEBUG: {last_error}")
                if exc.code in (429, 503):
                    saw_temporary_upstream_issue = True
                continue
            except Exception as exc:
                last_error = f"Verbindungsfehler {model_name} ({api_version}): {exc}"
                print(f"DEBUG: {last_error}")
                continue

    # Wenn wir hier landen, haben alle Versuche versagt
    if saw_temporary_upstream_issue:
        raise HTTPException(
            status_code=503,
            detail=f"KI-Dienst temporär nicht verfügbar (Quota/Überlastung). Letzter Fehler: {last_error}",
        )

    raise HTTPException(status_code=502, detail=f"KI-Dienst nicht erreichbar. Letzter Fehler: {last_error}")


def call_gemini_json(prompt: str, temperature: float = 0.25) -> Dict[str, Any]:
    raw = call_gemini_text(prompt, temperature=temperature)
    try:
        return extract_json_from_text(raw)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini JSON Parsing fehlgeschlagen: {exc}")


def collect_cover_letter_project(conn: sqlite3.Connection, project_id: str) -> Dict[str, Any]:
    project = require_project(conn, project_id)

    cur = conn.cursor()
    cur.execute("SELECT * FROM cover_letter_sources WHERE projectId=?", (project_id,))
    source_row = sqlite_row_to_dict(cur.fetchone()) or {}

    cur.execute(
        "SELECT * FROM cover_letter_resume_context_entries WHERE projectId=? ORDER BY sortOrder ASC, id ASC",
        (project_id,),
    )
    context_rows = [sqlite_row_to_dict(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT * FROM cover_letter_iterations WHERE projectId=? ORDER BY roundIndex ASC, createdAt ASC",
        (project_id,),
    )
    iteration_rows = [sqlite_row_to_dict(r) for r in cur.fetchall()]

    source_row["jobAnalysis"] = safe_json_loads(source_row.get("jobAnalysisJson"), {})
    source_row["resumeSummary"] = safe_json_loads(source_row.get("resumeSummaryJson"), {})
    source_row.pop("jobAnalysisJson", None)
    source_row.pop("resumeSummaryJson", None)

    for row in iteration_rows:
        row["scoreBreakdown"] = safe_json_loads(row.get("scoreBreakdownJson"), {})
        row["improvementHints"] = safe_json_loads(row.get("improvementHintsJson"), [])
        row.pop("scoreBreakdownJson", None)
        row.pop("improvementHintsJson", None)

    return {
        "project": project,
        "sources": source_row,
        "resumeContextEntries": context_rows,
        "iterations": iteration_rows,
    }


def build_resume_context_text(entries: List[Dict[str, Any]]) -> str:
    parts = []
    for idx, entry in enumerate(entries, start=1):
        fields = [
            f"Station {idx}",
            f"Firma: {entry.get('company', '')}",
            f"Rolle: {entry.get('role', '')}",
            f"Aufgaben: {entry.get('tasks', '')}",
            f"Erfahrungen: {entry.get('experiences', '')}",
            f"Was mochte ich: {entry.get('liked', '')}",
            f"Was war schwierig: {entry.get('disliked', '')}",
            f"Atmosphäre: {entry.get('atmosphere', '')}",
            f"Zusatz: {entry.get('notes', '')}",
        ]
        parts.append("\n".join(fields))
    return "\n\n".join(parts)


def generate_cover_letter_round(
    job_analysis: Dict[str, Any],
    baseline_letter: str,
    resume_text: str,
    resume_summary: Dict[str, Any],
    resume_context_entries: List[Dict[str, Any]],
    previous_draft: str,
    feedback: str,
) -> Dict[str, Any]:
    resume_context_text = build_resume_context_text(resume_context_entries)

    prompt = f"""
Du bist ein Bewerbungs-Coach-System. Erstelle und optimiere ein Anschreiben in deutscher Sprache.
Es MUSS die Bewerberperspektive authentisch bewahren.

WICHTIG FÜR DIE ANREDE:
Ansprechpartner aus der Stellenanzeige: {job_analysis.get('contactPerson') or 'Keiner gefunden'}
Falls ein Name oben steht (z.B. "Herr Schmidt"), verwende zwingend: "Sehr geehrter Herr Schmidt,".
Falls eine Dame (z.B. "Frau Müller"), verwende zwingend: "Sehr geehrte Frau Müller,".
Falls leer oder "Keiner gefunden", verwende zwingend: "Sehr geehrte Damen und Herren,".
IGNORIERE jeden anderen Namen, der eventuell im "Bisherigen Anschreiben" steht.

Verwende diese 4 Perspektiven für Bewertung und Verbesserung:
...

1) Bewerber-Treue (Authentizität, echte Stärken, glaubwürdige Sprache)
2) Recruiter-Fit (Klarheit, Relevanz, Struktur)
3) Fach-Fit (Passung auf Stelle/Anforderungen)
4) Konsistenz/Risiko (z.B. häufige Wechsel nachvollziehbar begründen, aber nicht überfokussieren)

Wenn es viele kurze Stationen gab: sachlich und glaubwürdig begründen, ohne defensive Überlänge.

Gib STRICT JSON zurück, OHNE Markdown:
{{
  "draft": "...",
  "scores": {{
    "applicant_voice": 1-10,
    "recruiter_fit": 1-10,
    "technical_fit": 1-10,
    "risk_consistency": 1-10
  }},
  "rationale": "kurze Begründung der Bewertung",
  "improvements": ["konkrete Verbesserung 1", "konkrete Verbesserung 2", "..."]
}}

Kontext Stellenanalyse (JSON):
{json.dumps(job_analysis, ensure_ascii=False)}

Bisheriges Anschreiben:
{baseline_letter}

Lebenslauf (Rohtext, gekürzt):
{resume_text[:8000]}

Lebenslauf-Zusammenfassung (JSON):
{json.dumps(resume_summary, ensure_ascii=False)}

Zusatz-Kontext aus Nutzer-Onboarding:
{resume_context_text[:7000]}

Vorheriger Entwurf:
{previous_draft}

Nutzer-Feedback für diese Runde:
{feedback or 'kein zusätzliches Feedback'}
""".strip()

    parsed = call_gemini_json(prompt, temperature=0.35)
    draft = str(parsed.get("draft") or "").strip()
    if not draft:
        raise HTTPException(status_code=502, detail="KI lieferte keinen Anschreiben-Entwurf")

    scores = parsed.get("scores") or {}
    score_breakdown = {
        "applicant_voice": clamp_score(scores.get("applicant_voice", 5)),
        "recruiter_fit": clamp_score(scores.get("recruiter_fit", 5)),
        "technical_fit": clamp_score(scores.get("technical_fit", 5)),
        "risk_consistency": clamp_score(scores.get("risk_consistency", 5)),
    }
    score_total = round(sum(score_breakdown.values()) / 4.0, 2)

    improvements = parsed.get("improvements")
    if not isinstance(improvements, list):
        improvements = []
    improvements = [str(item).strip() for item in improvements if str(item).strip()][:12]

    rationale = str(parsed.get("rationale") or "").strip()

    return {
        "draft": draft,
        "scoreTotal": score_total,
        "scoreBreakdown": score_breakdown,
        "rationale": rationale,
        "improvements": improvements,
    }


def run_cover_letter_auto_loop(
    conn: sqlite3.Connection,
    project_id: str,
    start_draft: str,
    target_score: float,
    max_rounds: int,
    feedback: str,
) -> Dict[str, Any]:
    bundle = collect_cover_letter_project(conn, project_id)
    source = bundle["sources"]
    resume_context_entries = bundle["resumeContextEntries"]

    job_analysis = source.get("jobAnalysis") or {}
    baseline_letter = source.get("baselineLetter") or ""
    resume_text = source.get("resumeText") or ""
    resume_summary = source.get("resumeSummary") or {}

    if not job_analysis:
        raise HTTPException(status_code=400, detail="Es fehlt eine Stellenanalyse")
    if not baseline_letter:
        raise HTTPException(status_code=400, detail="Es fehlt ein bestehendes Anschreiben")
    if not resume_text:
        raise HTTPException(status_code=400, detail="Es fehlt ein Lebenslauf")

    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(roundIndex), 0) FROM cover_letter_iterations WHERE projectId=?", (project_id,))
    max_existing = int(cur.fetchone()[0] or 0)

    current_draft = start_draft.strip() or baseline_letter
    current_feedback = (feedback or "").strip()
    rounds = []

    for step in range(1, max_rounds + 1):
        if step > 1:
            time.sleep(2) # Kurze Pause für Rate-Limits (RPM) im Free Tier
            
        result = generate_cover_letter_round(
            job_analysis=job_analysis,
            baseline_letter=baseline_letter,
            resume_text=resume_text,
            resume_summary=resume_summary,
            resume_context_entries=resume_context_entries,
            previous_draft=current_draft,
            feedback=current_feedback,
        )

        round_index = max_existing + step
        iteration_id = str(uuid.uuid4())
        created_at = now_iso()

        cur.execute(
            '''
            INSERT INTO cover_letter_iterations
            (id, projectId, roundIndex, draftText, scoreTotal, scoreBreakdownJson, rationale, improvementHintsJson, feedbackUsed, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                iteration_id,
                project_id,
                round_index,
                result["draft"],
                result["scoreTotal"],
                json.dumps(result["scoreBreakdown"], ensure_ascii=False),
                result["rationale"],
                json.dumps(result["improvements"], ensure_ascii=False),
                current_feedback,
                created_at,
            ),
        )

        rounds.append(
            {
                "iterationId": iteration_id,
                "roundIndex": round_index,
                "scoreTotal": result["scoreTotal"],
                "scoreBreakdown": result["scoreBreakdown"],
                "rationale": result["rationale"],
                "improvements": result["improvements"],
                "draft": result["draft"],
            }
        )

        current_draft = result["draft"]
        if result["scoreTotal"] >= target_score:
            break

        suggested = " | ".join(result["improvements"]) if result["improvements"] else ""
        current_feedback = (feedback or "")
        if suggested:
            current_feedback = (current_feedback + "\nVerbessere zusätzlich: " + suggested).strip()

    latest_score = rounds[-1]["scoreTotal"] if rounds else 0.0
    latest_draft = rounds[-1]["draft"] if rounds else current_draft

    cur.execute(
        """
        UPDATE cover_letter_projects
        SET latestDraft=?, latestScore=?, latestFeedback=?, updatedAt=?
        WHERE id=?
        """,
        (latest_draft, latest_score, feedback or "", now_iso(), project_id),
    )

    conn.commit()

    return {
        "rounds": rounds,
        "finalDraft": latest_draft,
        "finalScore": latest_score,
        "targetReached": bool(rounds and rounds[-1]["scoreTotal"] >= target_score),
        "targetScore": target_score,
    }


# --- Frontend Auslieferung ---
@app.get("/")
async def read_index():
    return FileResponse("index.html")


# --- API Endpunkte (bestehend) ---
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
    shifts_json = json.dumps(app_data.shifts.model_dump())

    history = [{"timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"), "event": "Bewerbung erstellt"}]
    history_json = json.dumps(history)
    status_date_value = app_data.statusDate.isoformat() if app_data.statusDate else ""

    c.execute(
        '''
        INSERT INTO applications
        (id, company, jobTitle, dateApplied, platform, salary, hourlyWage, isTempWork, shifts, status, statusDetail, statusDate, statusText, pdfPath, history)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            new_id,
            app_data.company,
            app_data.jobTitle,
            app_data.dateApplied.isoformat(),
            app_data.platform,
            app_data.salary,
            app_data.hourlyWage,
            1 if app_data.isTempWork else 0,
            shifts_json,
            app_data.status.value,
            app_data.statusDetail,
            status_date_value,
            app_data.statusText,
            app_data.pdfPath,
            history_json,
        ),
    )
    conn.commit()
    conn.close()
    return {"message": "Gespeichert", "id": new_id}


@app.put("/api/applications/{app_id}")
def update_application(app_id: str, app_data: Application):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = dict_factory
    c = conn.cursor()

    c.execute("SELECT * FROM applications WHERE id=?", (app_id,))
    old_app = c.fetchone()

    if not old_app:
        conn.close()
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    history = old_app.get("history", [])
    changes = []

    if old_app["status"] != app_data.status.value:
        changes.append(f"Status geändert: {old_app['status']} -> {app_data.status.value}")
    if old_app["company"] != app_data.company:
        changes.append("Firma geändert")
    if old_app["jobTitle"] != app_data.jobTitle:
        changes.append("Stelle geändert")
    if old_app["salary"] != app_data.salary:
        changes.append("Gehalt geändert")

    if changes:
        history.append(
            {
                "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "event": "; ".join(changes),
            }
        )

    shifts_json = json.dumps(app_data.shifts.model_dump())
    history_json = json.dumps(history)
    status_date_value = app_data.statusDate.isoformat() if app_data.statusDate else ""

    c.execute(
        '''
        UPDATE applications SET
        company=?, jobTitle=?, dateApplied=?, platform=?, salary=?, hourlyWage=?, isTempWork=?, shifts=?,
        status=?, statusDetail=?, statusDate=?, statusText=?, pdfPath=?, history=?
        WHERE id=?
        ''',
        (
            app_data.company,
            app_data.jobTitle,
            app_data.dateApplied.isoformat(),
            app_data.platform,
            app_data.salary,
            app_data.hourlyWage,
            1 if app_data.isTempWork else 0,
            shifts_json,
            app_data.status.value,
            app_data.statusDetail,
            status_date_value,
            app_data.statusText,
            app_data.pdfPath,
            history_json,
            app_id,
        ),
    )
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
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dateiname fehlt")

    safe_filename = sanitize_filename(file.filename)
    if not safe_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Nur PDF-Dateien sind erlaubt")

    if file.content_type not in ALLOWED_PDF_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Ungültiger Dateityp")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leere Datei")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Datei zu groß")

    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{safe_filename}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    extracted_text = ""
    try:
        extracted_text = extract_text_from_pdf_bytes(content)
    except Exception as e:
        print(f"Fehler beim PDF lesen: {e}")

    company_guess = ""
    job_guess = ""
    lines = [l.strip() for l in extracted_text.split("\n") if l.strip()]

    for i, line in enumerate(lines):
        if re.search(r"(?:Bewerbung als|Arbeitsplatz als)", line, re.IGNORECASE):
            job_match = re.search(r"(?:Bewerbung als|Arbeitsplatz als)\s+(.*)", line, re.IGNORECASE)
            if job_match:
                job_guess = job_match.group(1).strip()

            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if re.search(r"\b(?:GmbH|AG|e\.V\.|OHG|KG)\b", next_line):
                    company_guess = next_line
            if not company_guess and i - 1 >= 0:
                prev_line = lines[i - 1]
                if re.search(r"\b(?:GmbH|AG|e\.V\.|OHG|KG)\b", prev_line):
                    company_guess = prev_line
            if job_guess:
                break

    if not company_guess:
        for line in lines[5:]:
            if re.search(r"\b(?:GmbH|AG|e\.V\.|OHG|KG)\b", line):
                comp_match = re.search(r"(.*?\b(?:GmbH|AG|e\.V\.|OHG|KG)\b)", line)
                if comp_match:
                    company_guess = comp_match.group(1).strip()
                    break

    if company_guess:
        company_guess = re.sub(r"\d{5}\s+.*", "", company_guess).strip()

    if not job_guess:
        fallback_lines = [l.strip() for l in extracted_text.split("\n") if len(l.strip()) > 5]
        if fallback_lines:
            job_guess = fallback_lines[0]

    if not company_guess:
        company_guess = "Unbekannte Firma"

    return {
        "company": company_guess,
        "jobTitle": job_guess,
        "pdfPath": f"/pdfs/{filename}",
    }


# --- API Endpunkte (Cover Letter KI) ---
@app.post("/api/cover-letter/projects")
def create_cover_letter_project(payload: CoverLetterProjectCreate):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    project_id = str(uuid.uuid4())
    now = now_iso()
    cur.execute(
        '''
        INSERT INTO cover_letter_projects (id, createdAt, updatedAt, status, targetScore, maxRounds, latestDraft, latestScore, latestFeedback)
        VALUES (?, ?, ?, 'draft', ?, ?, '', 0.0, '')
        ''',
        (project_id, now, now, payload.targetScore, payload.maxRounds),
    )
    cur.execute("INSERT INTO cover_letter_sources (projectId) VALUES (?)", (project_id,))
    conn.commit()

    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return result


@app.get("/api/cover-letter/projects")
def list_cover_letter_projects():

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, createdAt, updatedAt, status, targetScore, maxRounds, latestScore
        FROM cover_letter_projects
        ORDER BY updatedAt DESC
        """
    )
    rows = [sqlite_row_to_dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT projectId, jobSourceType, jobSourceValue, jobAnalysisJson
        FROM cover_letter_sources
        """
    )
    source_rows = [sqlite_row_to_dict(r) for r in cur.fetchall()]
    sources_by_project = {r.get("projectId"): r for r in source_rows}

    for row in rows:
        source = sources_by_project.get(row.get("id"), {})
        analysis = safe_json_loads(source.get("jobAnalysisJson"), {})

        company_name = str(analysis.get("companyName") or "").strip()
        job_title = str(analysis.get("jobTitle") or "").strip()
        source_value = str(source.get("jobSourceValue") or "").strip()

        if company_name and job_title:
            project_label = f"{company_name} – {job_title}"
        elif job_title:
            project_label = job_title
        elif company_name:
            project_label = company_name
        elif source_value:
            project_label = source_value[:120]
        else:
            project_label = f"Projekt {str(row.get('id') or '')[:8]}"

        row["projectLabel"] = project_label
        row["companyName"] = company_name
        row["jobTitle"] = job_title

    conn.close()
    return {"projects": rows}


@app.get("/api/cover-letter/projects/{project_id}")
def get_cover_letter_project(project_id: str):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return result


@app.post("/api/cover-letter/projects/{project_id}/job-source")
def set_cover_letter_job_source(project_id: str, payload: CoverLetterJobSourceInput):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    require_project(conn, project_id)
    ensure_project_sources_row(conn, project_id)

    source_value = payload.sourceValue.strip()
    if payload.sourceType == "link":
        source_value = normalize_url(source_value)

    cur = conn.cursor()
    cur.execute(
        '''
        UPDATE cover_letter_sources
        SET jobSourceType=?, jobSourceValue=?, jobAnalysisJson=''
        WHERE projectId=?
        ''',
        (payload.sourceType, source_value, project_id),
    )

    if payload.sourceType == "text":
        analysis = analyze_job_text(source_value)
        if payload.contactPerson:
            analysis["contactPerson"] = payload.contactPerson
        cur.execute(
            "UPDATE cover_letter_sources SET jobAnalysisJson=? WHERE projectId=?",
            (json.dumps(analysis, ensure_ascii=False), project_id),
        )
    else:
        # Falls ein Link gespeichert wird, laden wir die bestehende Analyse (falls vorhanden) 
        # und aktualisieren nur den Ansprechpartner, falls übergeben.
        cur.execute("SELECT jobAnalysisJson FROM cover_letter_sources WHERE projectId=?", (project_id,))
        row = cur.fetchone()
        analysis = safe_json_loads(row[0] if row else "", {})
        if payload.contactPerson:
            analysis["contactPerson"] = payload.contactPerson
            cur.execute(
                "UPDATE cover_letter_sources SET jobAnalysisJson=? WHERE projectId=?",
                (json.dumps(analysis, ensure_ascii=False), project_id),
            )

    cur.execute("UPDATE cover_letter_projects SET updatedAt=? WHERE id=?", (now_iso(), project_id))
    conn.commit()

    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {"jobAnalysis": analysis, **result}


@app.post("/api/cover-letter/projects/{project_id}/analyze-job-link")
def analyze_cover_letter_job_link(project_id: str, payload: CoverLetterAnalyzeLinkInput):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    require_project(conn, project_id)
    ensure_project_sources_row(conn, project_id)

    cur = conn.cursor()
    cur.execute("SELECT * FROM cover_letter_sources WHERE projectId=?", (project_id,))
    source = sqlite_row_to_dict(cur.fetchone()) or {}

    url = payload.url or source.get("jobSourceValue")
    if not url:
        conn.close()
        raise HTTPException(status_code=400, detail="Keine URL vorhanden")

    normalized_url = normalize_url(url)
    html = fetch_url_html(normalized_url)
    parsed = html_to_text(html)
    analysis = analyze_job_text(parsed["text"], source_url=normalized_url, page_title=parsed["title"])

    cur.execute(
        '''
        UPDATE cover_letter_sources
        SET jobSourceType='link', jobSourceValue=?, jobAnalysisJson=?
        WHERE projectId=?
        ''',
        (normalized_url, json.dumps(analysis, ensure_ascii=False), project_id),
    )
    cur.execute("UPDATE cover_letter_projects SET updatedAt=? WHERE id=?", (now_iso(), project_id))
    conn.commit()

    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {
        "analysis": analysis,
        "contentPreview": parsed["text"][:4000],
        **result,
    }


@app.post("/api/cover-letter/projects/{project_id}/baseline-letter")
async def set_cover_letter_baseline(
    project_id: str,
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    require_project(conn, project_id)
    ensure_project_sources_row(conn, project_id)

    baseline_text = (text or "").strip()

    if file is not None:
        if not file.filename:
            conn.close()
            raise HTTPException(status_code=400, detail="Dateiname fehlt")
        safe_filename = sanitize_filename(file.filename)
        if not safe_filename.lower().endswith(".txt"):
            conn.close()
            raise HTTPException(status_code=400, detail="Anschreiben-Datei muss .txt sein")
        if file.content_type not in ALLOWED_TEXT_CONTENT_TYPES:
            conn.close()
            raise HTTPException(status_code=415, detail="Ungültiger Dateityp für Anschreiben")

        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            conn.close()
            raise HTTPException(status_code=413, detail="Datei zu groß")
        baseline_text = content.decode("utf-8", errors="ignore").strip()

    if not baseline_text:
        conn.close()
        raise HTTPException(status_code=400, detail="Anschreiben-Text fehlt")

    cur = conn.cursor()
    cur.execute(
        "UPDATE cover_letter_sources SET baselineLetter=? WHERE projectId=?",
        (baseline_text, project_id),
    )
    cur.execute("UPDATE cover_letter_projects SET updatedAt=? WHERE id=?", (now_iso(), project_id))
    conn.commit()

    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {
        "wordCount": len(re.findall(r"\S+", baseline_text)),
        **result,
    }


@app.post("/api/cover-letter/projects/{project_id}/resume")
async def set_cover_letter_resume(

    project_id: str,
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    require_project(conn, project_id)
    ensure_project_sources_row(conn, project_id)

    resume_text = (text or "").strip()
    source_type = "text"

    if file is not None:
        if not file.filename:
            conn.close()
            raise HTTPException(status_code=400, detail="Dateiname fehlt")

        safe_filename = sanitize_filename(file.filename)
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            conn.close()
            raise HTTPException(status_code=413, detail="Datei zu groß")

        if safe_filename.lower().endswith(".pdf"):
            source_type = "pdf"
            try:
                resume_text = extract_text_from_pdf_bytes(content)
            except Exception as exc:
                conn.close()
                raise HTTPException(status_code=400, detail=f"Lebenslauf-PDF konnte nicht gelesen werden: {exc}")
        elif safe_filename.lower().endswith(".txt"):
            source_type = "txt"
            resume_text = content.decode("utf-8", errors="ignore").strip()
        else:
            conn.close()
            raise HTTPException(status_code=400, detail="Lebenslauf muss .txt oder .pdf sein")

    if not resume_text:
        conn.close()
        raise HTTPException(status_code=400, detail="Lebenslauf-Text fehlt")

    summary = parse_resume_summary(resume_text)

    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cover_letter_sources
        SET resumeText=?, resumeSourceType=?, resumeSummaryJson=?
        WHERE projectId=?
        """,
        (resume_text, source_type, json.dumps(summary, ensure_ascii=False), project_id),
    )
    cur.execute("UPDATE cover_letter_projects SET updatedAt=? WHERE id=?", (now_iso(), project_id))
    conn.commit()

    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {
        "resumeSummary": summary,
        **result,
    }


@app.post("/api/cover-letter/projects/{project_id}/resume-context")
def set_cover_letter_resume_context(project_id: str, payload: CoverLetterResumeContextInput):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    require_project(conn, project_id)

    cur = conn.cursor()
    cur.execute("DELETE FROM cover_letter_resume_context_entries WHERE projectId=?", (project_id,))

    for idx, entry in enumerate(payload.entries):
        cur.execute(
            '''
            INSERT INTO cover_letter_resume_context_entries
            (id, projectId, sortOrder, company, role, tasks, experiences, liked, disliked, atmosphere, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                str(uuid.uuid4()),
                project_id,
                idx,
                entry.company or "",
                entry.role or "",
                entry.tasks or "",
                entry.experiences or "",
                entry.liked or "",
                entry.disliked or "",
                entry.atmosphere or "",
                entry.notes or "",
            ),
        )

    cur.execute("UPDATE cover_letter_projects SET updatedAt=? WHERE id=?", (now_iso(), project_id))
    conn.commit()

    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {"savedEntries": len(payload.entries), **result}


@app.post("/api/cover-letter/projects/{project_id}/generate")
def generate_cover_letter(project_id: str, payload: CoverLetterGenerateInput = CoverLetterGenerateInput()):

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    project = require_project(conn, project_id)
    bundle = collect_cover_letter_project(conn, project_id)

    target_score = clamp_score(payload.targetScore if payload.targetScore is not None else project.get("targetScore", COVER_LETTER_TARGET_SCORE))
    max_rounds_raw = payload.maxRounds if payload.maxRounds is not None else project.get("maxRounds", COVER_LETTER_MAX_ROUNDS)
    max_rounds = max(1, min(20, int(max_rounds_raw)))

    start = bundle["sources"].get("baselineLetter") or project.get("latestDraft") or ""
    feedback = project.get("latestFeedback") or ""

    result = run_cover_letter_auto_loop(
        conn=conn,
        project_id=project_id,
        start_draft=start,
        target_score=target_score,
        max_rounds=max_rounds,
        feedback=feedback,
    )

    snapshot = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {"result": result, **snapshot}


@app.post("/api/cover-letter/projects/{project_id}/feedback")
def add_cover_letter_feedback(project_id: str, payload: CoverLetterFeedbackInput):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    require_project(conn, project_id)

    cur = conn.cursor()
    cur.execute(
        "UPDATE cover_letter_projects SET latestFeedback=?, updatedAt=? WHERE id=?",
        (payload.feedback, now_iso(), project_id),
    )
    conn.commit()

    snapshot = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {"message": "Feedback gespeichert", **snapshot}


@app.post("/api/cover-letter/projects/{project_id}/iterate")
def iterate_cover_letter(project_id: str, payload: CoverLetterIterateInput = CoverLetterIterateInput()):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    project = require_project(conn, project_id)
    bundle = collect_cover_letter_project(conn, project_id)

    target_score = clamp_score(payload.targetScore if payload.targetScore is not None else project.get("targetScore", COVER_LETTER_TARGET_SCORE))
    max_rounds_raw = payload.maxRounds if payload.maxRounds is not None else project.get("maxRounds", COVER_LETTER_MAX_ROUNDS)
    max_rounds = max(1, min(20, int(max_rounds_raw)))

    start = project.get("latestDraft") or bundle["sources"].get("baselineLetter") or ""
    feedback = (payload.feedback or project.get("latestFeedback") or "").strip()

    result = run_cover_letter_auto_loop(
        conn=conn,
        project_id=project_id,
        start_draft=start,
        target_score=target_score,
        max_rounds=max_rounds,
        feedback=feedback,
    )

    snapshot = collect_cover_letter_project(conn, project_id)
    conn.close()
    return {"result": result, **snapshot}


@app.get("/api/cover-letter/projects/{project_id}/export/txt")
def export_cover_letter_txt(project_id: str):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    project = require_project(conn, project_id)
    bundle = collect_cover_letter_project(conn, project_id)

    source = bundle["sources"]
    analysis = source.get("jobAnalysis") or {}
    draft = project.get("latestDraft") or ""
    if not draft:
        iterations = bundle.get("iterations") or []
        if iterations:
            draft = iterations[-1].get("draftText") or ""
        if not draft:
            draft = source.get("baselineLetter") or ""

    if not draft:
        conn.close()
        raise HTTPException(status_code=400, detail="Kein Anschreiben zum Exportieren vorhanden")

    header_lines = [
        "ANSCHREIBEN EXPORT",
        "=" * 40,
        f"Projekt-ID: {project_id}",
        f"Erstellt: {now_iso()}",
    ]
    if analysis.get("companyName"):
        header_lines.append(f"Firma: {analysis.get('companyName')}")
    if analysis.get("jobTitle"):
        header_lines.append(f"Stelle: {analysis.get('jobTitle')}")

    content = "\n".join(header_lines) + "\n\n" + draft.strip() + "\n"

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cover_letter_exports (id, projectId, exportType, content, createdAt) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), project_id, "txt", content, now_iso()),
    )
    conn.commit()
    conn.close()

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="anschreiben_{project_id[:8]}.txt"'
        },
    )


# --- API Endpunkte (Bewerberprofile) ---
@app.get("/api/profiles")
def list_profiles():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, profileName, updatedAt FROM applicant_profiles ORDER BY updatedAt DESC")
    rows = [sqlite_row_to_dict(r) for r in cur.fetchall()]
    conn.close()
    return {"profiles": rows}


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM applicant_profiles WHERE id=?", (profile_id,))
    profile_row = sqlite_row_to_dict(cur.fetchone())
    if not profile_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")
    
    cur.execute(
        "SELECT * FROM applicant_profile_context_entries WHERE profileId=? ORDER BY sortOrder ASC, id ASC",
        (profile_id,)
    )
    context_rows = [sqlite_row_to_dict(r) for r in cur.fetchall()]
    
    profile_row["resumeSummary"] = safe_json_loads(profile_row.get("resumeSummaryJson"), {})
    profile_row.pop("resumeSummaryJson", None)
    
    conn.close()
    return {**profile_row, "contextEntries": context_rows}


@app.post("/api/profiles")
def save_profile(payload: ApplicantProfileInput):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    
    profile_id = payload.id or str(uuid.uuid4())
    now = now_iso()
    
    summary = parse_resume_summary(payload.resumeText or "")
    
    # Check if update or create
    cur.execute("SELECT id FROM applicant_profiles WHERE id=?", (profile_id,))
    if cur.fetchone():
        cur.execute(
            "UPDATE applicant_profiles SET profileName=?, resumeText=?, resumeSummaryJson=?, baselineLetter=?, updatedAt=? WHERE id=?",
            (payload.profileName, payload.resumeText, json.dumps(summary, ensure_ascii=False), payload.baselineLetter, now, profile_id)
        )
    else:
        cur.execute(
            "INSERT INTO applicant_profiles (id, profileName, resumeText, resumeSummaryJson, baselineLetter, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
            (profile_id, payload.profileName, payload.resumeText, json.dumps(summary, ensure_ascii=False), payload.baselineLetter, now)
        )
    
    cur.execute("DELETE FROM applicant_profile_context_entries WHERE profileId=?", (profile_id,))
    for idx, entry in enumerate(payload.contextEntries):
        cur.execute(
            '''
            INSERT INTO applicant_profile_context_entries
            (id, profileId, sortOrder, company, role, tasks, experiences, liked, disliked, atmosphere, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                str(uuid.uuid4()),
                profile_id,
                idx,
                entry.company or "",
                entry.role or "",
                entry.tasks or "",
                entry.experiences or "",
                entry.liked or "",
                entry.disliked or "",
                entry.atmosphere or "",
                entry.notes or "",
            )
        )
    
    conn.commit()
    conn.close()
    return {"id": profile_id, "message": "Profil gespeichert"}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM applicant_profile_context_entries WHERE profileId=?", (profile_id,))
    cur.execute("DELETE FROM applicant_profiles WHERE id=?", (profile_id,))
    conn.commit()
    conn.close()
    return {"message": "Profil gelöscht"}


@app.post("/api/cover-letter/projects/{project_id}/apply-profile/{profile_id}")
def apply_profile_to_project(project_id: str, profile_id: str):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    require_project(conn, project_id)
    ensure_project_sources_row(conn, project_id)
    
    cur.execute("SELECT * FROM applicant_profiles WHERE id=?", (profile_id,))
    profile = sqlite_row_to_dict(cur.fetchone())
    if not profile:
        conn.close()
        raise HTTPException(status_code=404, detail="Profil nicht gefunden")
    
    cur.execute("SELECT * FROM applicant_profile_context_entries WHERE profileId=? ORDER BY sortOrder ASC", (profile_id,))
    context_entries = [sqlite_row_to_dict(r) for r in cur.fetchall()]
    
    # Update project sources
    cur.execute(
        "UPDATE cover_letter_sources SET resumeText=?, resumeSummaryJson=?, baselineLetter=? WHERE projectId=?",
        (profile["resumeText"], profile["resumeSummaryJson"], profile["baselineLetter"], project_id)
    )
    
    # Update project context entries
    cur.execute("DELETE FROM cover_letter_resume_context_entries WHERE projectId=?", (project_id,))
    for idx, entry in enumerate(context_entries):
        cur.execute(
            '''
            INSERT INTO cover_letter_resume_context_entries
            (id, projectId, sortOrder, company, role, tasks, experiences, liked, disliked, atmosphere, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                str(uuid.uuid4()),
                project_id,
                idx,
                entry.get("company") or "",
                entry.get("role") or "",
                entry.get("tasks") or "",
                entry.get("experiences") or "",
                entry.get("liked") or "",
                entry.get("disliked") or "",
                entry.get("atmosphere") or "",
                entry.get("notes") or "",
            )
        )
    
    cur.execute("UPDATE cover_letter_projects SET updatedAt=? WHERE id=?", (now_iso(), project_id))
    conn.commit()
    
    result = collect_cover_letter_project(conn, project_id)
    conn.close()
    return result


@app.post("/api/cover-letter/projects/{project_id}/resume-context-extract")
async def extract_resume_context_from_file(
    project_id: str,
    file: UploadFile = File(...)
):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    require_project(conn, project_id)

    filename = file.filename.lower()
    content = await file.read()
    raw_text = ""

    if filename.endswith(".txt"):
        raw_text = content.decode("utf-8", errors="ignore").strip()
    elif filename.endswith(".pdf"):
        try:
            raw_text = extract_text_from_pdf_bytes(content)
        except Exception as e:
            conn.close()
            raise HTTPException(status_code=400, detail=f"PDF konnte nicht gelesen werden: {e}")
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Nur .txt oder .pdf Dateien werden unterstützt")

    if not raw_text:
        conn.close()
        raise HTTPException(status_code=400, detail="Die Datei enthält keinen lesbaren Text")

    prompt = f"""
Analysiere den folgenden Text über berufliche Erfahrungen und extrahiere strukturierte Informationen für verschiedene Stationen.
Gib STRICT JSON zurück. Keine Markdown-Formatierung.
Struktur: {{"entries": [{{ "company": "...", "role": "...", "tasks": "...", "experiences": "...", "liked": "...", "disliked": "...", "atmosphere": "...", "notes": "..." }}]}}

Text:
{raw_text[:10000]}
"""
    
    try:
        extracted = call_gemini_json(prompt)
        entries = extracted.get("entries") or []
        if not isinstance(entries, list):
            entries = []
    except HTTPException:
        # Re-raise existing HTTPExceptions (like Gemini API errors)
        conn.close()
        raise
    except Exception as exc:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Unerwarteter Fehler bei KI-Extraktion: {exc}")

    conn.close()
    return {"entries": entries}


@app.get("/api/ai-instructions")
def get_ai_instructions():
    return {
        "endpoint": "/api/applications",
        "method": "GET",
        "description": "Nutze diesen Endpunkt, um alle Bewerbungsdaten abzurufen.",
        "usage_for_llm": "Du bist ein Karriere-Assistent. Analysiere die Liste der Bewerbungen unter /api/applications.",
        "cover_letter_endpoints": [
            "/api/cover-letter/projects",
            "/api/cover-letter/projects/{id}",
            "/api/cover-letter/projects/{id}/generate",
            "/api/cover-letter/projects/{id}/iterate",
        ],
    }
