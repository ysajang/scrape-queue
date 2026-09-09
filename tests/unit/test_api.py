"""API contract tests.

These run without a database: every route that would touch one is exercised
through its guards, which is where the interesting behaviour is.
"""

import pytest
from fastapi.testclient import TestClient

from scrapequeue.api.app import app
from scrapequeue.api.middleware.auth import Principal


@pytest.fixture
def client():
    return TestClient(app)


def test_healthz_needs_no_auth(client):
    assert client.get("/healthz").status_code == 200


def test_metrics_are_exposed(client):
    body = client.get("/metrics").text
    assert "sq_jobs_submitted_total" in body


def test_submitting_without_a_key_is_rejected(client):
    assert client.post("/jobs", json={"target": "federal_register"}).status_code == 401


def test_read_key_cannot_submit(client):
    response = client.post(
        "/jobs", json={"target": "federal_register"}, headers={"X-API-Key": "test-read"}
    )
    assert response.status_code == 403


def test_unknown_target_is_a_404(client):
    response = client.post(
        "/jobs", json={"target": "nosuchtarget"}, headers={"X-API-Key": "test-write"}
    )
    assert response.status_code == 404


def test_target_names_cannot_escape_the_targets_directory(client):
    response = client.post(
        "/jobs", json={"target": "../../etc/passwd"}, headers={"X-API-Key": "test-write"}
    )
    assert response.status_code == 422


def test_unknown_fields_are_rejected(client):
    response = client.post(
        "/jobs",
        json={"target": "federal_register", "surprise": 1},
        headers={"X-API-Key": "test-write"},
    )
    assert response.status_code == 422


def test_max_pages_is_bounded(client):
    response = client.post(
        "/jobs",
        json={"target": "federal_register", "max_pages": 100_000},
        headers={"X-API-Key": "test-write"},
    )
    assert response.status_code == 422


def test_scope_ordering():
    assert Principal("k", "admin").allows("write")
    assert not Principal("k", "read").allows("write")


def test_openapi_documents_every_route(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/jobs", "/jobs/{job_id}", "/jobs/{job_id}/result"} <= set(paths)
