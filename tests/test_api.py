from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api


def _payload(**overrides):
    data = {
        "company": "Beispiel GmbH",
        "jobTitle": "Industriemechaniker",
        "dateApplied": "2026-04-20",
        "platform": "https://example.com/job",
        "salary": "3200 € Brutto",
        "hourlyWage": 20.0,
        "isTempWork": False,
        "shifts": {"morning": True, "late": False, "night": False},
        "status": "offen",
        "statusDetail": "",
        "statusDate": "",
        "statusText": "",
        "pdfPath": "",
    }
    data.update(overrides)
    return data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "bewerbungen_test.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setattr(api, "DB_FILE", str(test_db))
    monkeypatch.setattr(api, "UPLOAD_DIR", str(uploads_dir))

    api.init_db()
    with TestClient(api.app) as test_client:
        yield test_client


def test_get_applications_starts_empty(client):
    response = client.get("/api/applications")

    assert response.status_code == 200
    assert response.json() == []


def test_create_and_list_application(client):
    create_response = client.post("/api/applications", json=_payload())

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["message"] == "Gespeichert"
    assert created["id"]

    list_response = client.get("/api/applications")
    assert list_response.status_code == 200
    items = list_response.json()
    assert len(items) == 1
    assert items[0]["company"] == "Beispiel GmbH"
    assert items[0]["jobTitle"] == "Industriemechaniker"
    assert items[0]["history"][0]["event"] == "Bewerbung erstellt"


def test_update_application_appends_history_on_changes(client):
    create_response = client.post("/api/applications", json=_payload())
    app_id = create_response.json()["id"]

    update_body = _payload(status="gemeldet", salary="3500 € Brutto", statusDetail="Telefonat")
    update_response = client.put(f"/api/applications/{app_id}", json=update_body)

    assert update_response.status_code == 200
    assert update_response.json()["message"] == "Aktualisiert"

    list_response = client.get("/api/applications")
    item = list_response.json()[0]
    assert item["status"] == "gemeldet"
    assert item["salary"] == "3500 € Brutto"
    assert len(item["history"]) >= 2
    assert "Status geändert" in item["history"][-1]["event"]


def test_update_unknown_application_returns_404(client):
    response = client.put(f"/api/applications/missing-id", json=_payload())

    assert response.status_code == 404
    assert response.json()["detail"] == "Nicht gefunden"


def test_delete_application(client):
    create_response = client.post("/api/applications", json=_payload())
    app_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/applications/{app_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Gelöscht"

    list_response = client.get("/api/applications")
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_extract_endpoint_returns_parsed_company_and_job(client, monkeypatch):
    class DummyPage:
        def extract_text(self):
            return "Bewerbung als Zerspanungsmechaniker\nMusterfirma GmbH"

    class DummyReader:
        def __init__(self, _filepath):
            self.pages = [DummyPage()]

    monkeypatch.setattr(api, "PdfReader", DummyReader)

    response = client.post(
        "/api/extract",
        files={"file": ("bewerbung.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["company"] == "Musterfirma GmbH"
    assert payload["jobTitle"] == "Zerspanungsmechaniker"
    assert payload["pdfPath"].startswith("/pdfs/")

    uploads_path = Path(api.UPLOAD_DIR)
    assert any(uploads_path.iterdir())


def test_create_application_validation_error(client):
    response = client.post("/api/applications", json={"company": "X"})

    assert response.status_code == 422
