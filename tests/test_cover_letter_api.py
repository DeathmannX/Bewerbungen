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


def test_cover_letter_generate_requires_gemini_without_mock(client):
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
    assert gen_res.status_code == 503
    assert "GEMINI_API_KEY" in gen_res.json()["detail"]


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
