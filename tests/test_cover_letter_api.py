import api
from fastapi.testclient import TestClient
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "bewerbungen_cover_letter_test.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setattr(api, "DB_FILE", str(test_db))
    monkeypatch.setattr(api, "UPLOAD_DIR", str(uploads_dir))
    monkeypatch.setattr(api, "MAX_UPLOAD_SIZE_BYTES", 1024 * 1024)

    api.init_db()
    with TestClient(api.app) as test_client:
        yield test_client


def test_cover_letter_project_end_to_end_generate_and_export(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={"targetScore": 9, "maxRounds": 5})
    assert create_res.status_code == 200
    project_id = create_res.json()["project"]["id"]

    job_text = """
    KNDS GmbH
    Wir suchen Industriemechaniker (m/w/d)
    Anforderungen: Erfahrung in Montage, Wartung und Teamarbeit.
    """
    source_res = client.post(
        f"/api/cover-letter/projects/{project_id}/job-source",
        json={"sourceType": "text", "sourceValue": job_text},
    )
    assert source_res.status_code == 200
    assert source_res.json()["sources"]["jobAnalysis"]["companyName"] != ""

    baseline_res = client.post(
        f"/api/cover-letter/projects/{project_id}/baseline-letter",
        data={"text": "Hiermit bewerbe ich mich als Industriemechaniker."},
    )
    assert baseline_res.status_code == 200

    resume_res = client.post(
        f"/api/cover-letter/projects/{project_id}/resume",
        data={"text": "2019-2021 Firma A Industriemechaniker\n2022-2024 Firma B Montage"},
    )
    assert resume_res.status_code == 200
    assert "resumeSummary" in resume_res.json()

    context_res = client.post(
        f"/api/cover-letter/projects/{project_id}/resume-context",
        json={
            "entries": [
                {
                    "company": "Firma A",
                    "role": "Industriemechaniker",
                    "tasks": "Wartung und Montage",
                    "experiences": "viel mit Hydraulik gearbeitet",
                    "liked": "Teamarbeit",
                    "disliked": "zu wenig Entwicklung",
                    "atmosphere": "kollegial",
                    "notes": "frühe Schichten waren okay",
                }
            ]
        },
    )
    assert context_res.status_code == 200
    assert context_res.json()["savedEntries"] == 1

    def fake_gemini_json(_prompt, temperature=0.25):
        _ = temperature
        return {
            "draft": "Sehr geehrte Damen und Herren, ich bewerbe mich motiviert bei KNDS als Industriemechaniker.",
            "scores": {
                "applicant_voice": 9.0,
                "recruiter_fit": 9.0,
                "technical_fit": 9.0,
                "risk_consistency": 9.0,
            },
            "rationale": "Sehr gute Passung und klare Motivation.",
            "improvements": ["Konkretes Praxisbeispiel ergänzen."],
        }

    monkeypatch.setattr(api, "call_gemini_json", fake_gemini_json)

    gen_res = client.post(
        f"/api/cover-letter/projects/{project_id}/generate",
        json={"targetScore": 9, "maxRounds": 3},
    )
    assert gen_res.status_code == 200
    payload = gen_res.json()
    assert payload["result"]["finalScore"] >= 9
    assert len(payload["result"]["rounds"]) == 1
    assert payload["project"]["latestDraft"] != ""

    export_res = client.get(f"/api/cover-letter/projects/{project_id}/export/txt")
    assert export_res.status_code == 200
    assert "ANSCHREIBEN EXPORT" in export_res.text
    assert "KNDS" in export_res.text


def test_cover_letter_analyze_link_uses_target_page(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={})
    project_id = create_res.json()["project"]["id"]

    client.post(
        f"/api/cover-letter/projects/{project_id}/job-source",
        json={"sourceType": "link", "sourceValue": "https://example.com/job"},
    )

    html = """
    <html>
      <head><title>Industriemechaniker (m/w/d) bei KNDS GmbH</title></head>
      <body>
        <h1>Wir suchen Industriemechaniker (m/w/d)</h1>
        <p>KNDS GmbH ist ein internationaler Hersteller.</p>
        <p>Anforderungen: Wartung, Montage, Schichtbereitschaft.</p>
      </body>
    </html>
    """

    monkeypatch.setattr(api, "fetch_url_html", lambda _url: html)

    analyze_res = client.post(f"/api/cover-letter/projects/{project_id}/analyze-job-link", json={})
    assert analyze_res.status_code == 200
    analysis = analyze_res.json()["analysis"]
    assert analysis["jobTitle"] != ""
    assert analysis["companyName"] != ""


def test_cover_letter_generate_requires_gemini_without_mock(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    create_res = client.post("/api/cover-letter/projects", json={})
    project_id = create_res.json()["project"]["id"]

    client.post(
        f"/api/cover-letter/projects/{project_id}/job-source",
        json={"sourceType": "text", "sourceValue": "Wir suchen Industriemechaniker bei KNDS GmbH"},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/baseline-letter",
        data={"text": "Basis-Anschreiben"},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/resume",
        data={"text": "Lebenslauftext 2020-2024"},
    )

    gen_res = client.post(f"/api/cover-letter/projects/{project_id}/generate", json={})
    assert gen_res.status_code == 200
    payload = gen_res.json()
    assert payload["result"]["rounds"]
    assert "Fallback" in (payload["result"]["rounds"][0]["rationale"] or "")


def test_call_gemini_text_maps_upstream_503_to_503(monkeypatch):
    import io
    from urllib.error import HTTPError

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")

    def fake_urlopen(_req, timeout=90):
        _ = timeout
        raise HTTPError(
            url="https://example.invalid",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b"{\"error\":{\"code\":503,\"status\":\"UNAVAILABLE\"}}"),
        )

    monkeypatch.setattr(api, "urlopen", fake_urlopen)

    with pytest.raises(api.HTTPException) as exc_info:
        api.call_gemini_text("test prompt")

    assert exc_info.value.status_code == 503


def test_list_projects_returns_human_readable_label(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={})
    assert create_res.status_code == 200
    project_id = create_res.json()["project"]["id"]

    monkeypatch.setattr(
        api,
        "analyze_job_text",
        lambda _text, source_url="", page_title="": {
            "sourceUrl": source_url,
            "pageTitle": page_title,
            "companyName": "Airbus Helicopters Technik GmbH",
            "jobTitle": "Industriemechaniker – dynamische Komponenten",
            "requirements": ["Wartung", "Montage"],
            "companySummary": "Luftfahrttechnik",
            "customers": "Aviation",
            "isTempWork": False,
            "contactPerson": "Frau Müller",
        },
    )

    save_source = client.post(
        f"/api/cover-letter/projects/{project_id}/job-source",
        json={"sourceType": "text", "sourceValue": "dummy"},
    )
    assert save_source.status_code == 200

    list_res = client.get("/api/cover-letter/projects")
    assert list_res.status_code == 200
    rows = list_res.json()["projects"]
    row = next((r for r in rows if r["id"] == project_id), None)

    assert row is not None
    assert row["companyName"] == "Airbus Helicopters Technik GmbH"
    assert row["jobTitle"] == "Industriemechaniker – dynamische Komponenten"
    assert row["projectLabel"] == "Airbus Helicopters Technik GmbH – Industriemechaniker – dynamische Komponenten"


def test_apply_profile_to_project_accepts_profile_context_dict_rows(client):
    create_res = client.post("/api/cover-letter/projects", json={})
    assert create_res.status_code == 200
    project_id = create_res.json()["project"]["id"]

    profile_res = client.post(
        "/api/profiles",
        json={
            "profileName": "Industriemechaniker",
            "resumeText": "2019-2024 Praxis",
            "baselineLetter": "Basistext",
            "contextEntries": [
                {
                    "company": "Firma A",
                    "role": "Industriemechaniker",
                    "tasks": "Wartung",
                    "experiences": "Hydraulik",
                    "liked": "Teamarbeit",
                    "disliked": "Schichtwechsel",
                    "atmosphere": "kollegial",
                    "notes": "Sicherheitsbewusst",
                }
            ],
        },
    )
    assert profile_res.status_code == 200
    profile_id = profile_res.json()["id"]

    apply_res = client.post(f"/api/cover-letter/projects/{project_id}/apply-profile/{profile_id}")
    assert apply_res.status_code == 200
    payload = apply_res.json()

    assert payload["sources"]["resumeText"] == "2019-2024 Praxis"
    assert payload["sources"]["baselineLetter"] == "Basistext"
    assert len(payload["resumeContextEntries"]) == 1
    assert payload["resumeContextEntries"][0]["company"] == "Firma A"


def test_cover_letter_iterate_with_feedback_uses_multiple_rounds(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={"targetScore": 9, "maxRounds": 5})
    project_id = create_res.json()["project"]["id"]

    client.post(
        f"/api/cover-letter/projects/{project_id}/job-source",
        json={"sourceType": "text", "sourceValue": "KNDS GmbH sucht Industriemechaniker. Anforderungen: Montage."},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/baseline-letter",
        data={"text": "Ich bewerbe mich hiermit."},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/resume",
        data={"text": "2018-2020 Firma C Montage\n2020-2024 Firma D Wartung"},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/feedback",
        json={"feedback": "Bitte mehr technische Tiefe und konkretere Beispiele."},
    )

    calls = {"n": 0}

    def fake_gemini_json(_prompt, temperature=0.25):
        _ = temperature
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "draft": "Runde 1 Entwurf",
                "scores": {
                    "applicant_voice": 7,
                    "recruiter_fit": 7,
                    "technical_fit": 7,
                    "risk_consistency": 7,
                },
                "rationale": "Noch zu allgemein.",
                "improvements": ["Mehr technische Beispiele", "Konkrete Erfolge nennen"],
            }
        return {
            "draft": "Runde 2 Entwurf",
            "scores": {
                "applicant_voice": 9.2,
                "recruiter_fit": 9.0,
                "technical_fit": 9.1,
                "risk_consistency": 9.0,
            },
            "rationale": "Jetzt deutlich passender.",
            "improvements": ["Optional: Einstieg noch kürzen"],
        }

    monkeypatch.setattr(api, "call_gemini_json", fake_gemini_json)

    iterate_res = client.post(
        f"/api/cover-letter/projects/{project_id}/iterate",
        json={"targetScore": 9, "maxRounds": 4},
    )
    assert iterate_res.status_code == 200
    result = iterate_res.json()["result"]
    assert result["targetReached"] is True
    assert len(result["rounds"]) == 2
    assert result["finalDraft"] == "Runde 2 Entwurf"


def test_station_blueprint_contains_key_missing_stations(client):
    res = client.get("/api/cover-letter/station-blueprint")
    assert res.status_code == 200
    entries = res.json()["entries"]
    assert len(entries) >= 10

    companies = [e["company"] for e in entries]
    assert any("Rehadapt" in c for c in companies)
    assert any("Adecco" in c for c in companies)


def test_resume_context_extract_text_uses_voice_as_fill_help(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={})
    project_id = create_res.json()["project"]["id"]

    monkeypatch.setattr(api, "call_gemini_json", lambda _prompt, temperature=0.25: {"entries": []})

    res = client.post(
        f"/api/cover-letter/projects/{project_id}/resume-context-extract-text",
        json={
            "resumeText": "01.03.2025 – 31.08.2025 Rehadapt GmbH in Kassel\nEinrichten von CNC Maschinen",
            "voiceText": "Adecco (KVG Kassel): einstellen der Schienenbremsen und Gewichtsgeber",
        },
    )
    assert res.status_code == 200
    entries = res.json()["entries"]

    companies = " | ".join(e.get("company", "") for e in entries)
    roles = " | ".join(e.get("role", "") for e in entries)
    tasks = " | ".join(e.get("tasks", "") for e in entries)

    assert "Rehadapt" in companies
    assert "Adecco" in companies
    assert "CNC" in tasks or "CNC" in roles


def test_resume_context_extract_filters_empty_entries(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={})
    project_id = create_res.json()["project"]["id"]

    monkeypatch.setattr(
        api,
        "call_gemini_json",
        lambda _prompt, temperature=0.25: {
            "entries": [
                {"company": "", "role": "", "tasks": "", "experiences": "", "liked": "", "disliked": "", "atmosphere": "", "notes": ""},
                {"company": "Team Time GmbH", "role": "Produktionsmitarbeiter", "tasks": "Liner vermessen", "experiences": "", "liked": "", "disliked": "", "atmosphere": "", "notes": ""},
            ]
        },
    )

    res = client.post(
        f"/api/cover-letter/projects/{project_id}/resume-context-extract-text",
        json={"resumeText": "Team Time GmbH (Kunde Hexagon Purus)\nLinerhälften eingangsvermessen"},
    )
    assert res.status_code == 200
    entries = res.json()["entries"]

    assert len(entries) >= 1
    assert all((e.get("company") or "").strip() or (e.get("role") or "").strip() for e in entries)


def test_generate_uses_offline_fallback_on_upstream_503(client, monkeypatch):
    create_res = client.post("/api/cover-letter/projects", json={})
    project_id = create_res.json()["project"]["id"]

    client.post(
        f"/api/cover-letter/projects/{project_id}/job-source",
        json={"sourceType": "text", "sourceValue": "KNDS GmbH sucht Industriemechaniker"},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/baseline-letter",
        data={"text": "Hiermit bewerbe ich mich als Industriemechaniker."},
    )
    client.post(
        f"/api/cover-letter/projects/{project_id}/resume",
        data={"text": "2019-2024 Industriemechaniker mit CNC-Erfahrung."},
    )

    def _raise_503(_prompt, temperature=0.25):
        _ = temperature
        raise api.HTTPException(status_code=503, detail="Quota überschritten")

    monkeypatch.setattr(api, "call_gemini_json", _raise_503)

    res = client.post(f"/api/cover-letter/projects/{project_id}/generate", json={})
    assert res.status_code == 200
    payload = res.json()
    assert payload["result"]["rounds"]
    assert "Fallback" in (payload["result"]["rounds"][0]["rationale"] or "")


def test_list_projects_hides_empty_placeholders(client, monkeypatch):
    empty_res = client.post("/api/cover-letter/projects", json={})
    empty_id = empty_res.json()["project"]["id"]

    filled_res = client.post("/api/cover-letter/projects", json={})
    filled_id = filled_res.json()["project"]["id"]

    monkeypatch.setattr(
        api,
        "analyze_job_text",
        lambda _text, source_url="", page_title="": {
            "sourceUrl": source_url,
            "pageTitle": page_title,
            "companyName": "Muster GmbH",
            "jobTitle": "Industriemechaniker",
            "requirements": [],
            "companySummary": "",
            "customers": "",
            "isTempWork": False,
            "contactPerson": "",
        },
    )

    client.post(
        f"/api/cover-letter/projects/{filled_id}/job-source",
        json={"sourceType": "text", "sourceValue": "dummy"},
    )

    list_res = client.get("/api/cover-letter/projects")
    assert list_res.status_code == 200
    ids = [p["id"] for p in list_res.json()["projects"]]
    assert filled_id in ids
    assert empty_id not in ids
