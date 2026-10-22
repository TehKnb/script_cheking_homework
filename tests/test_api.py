from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, get_checker
from app.services.pipeline import DocumentChecker
from tests.builders import DocSpec, FakeOcr, build_docx, funnel_image, funnel_tokens, header_parts
from tests.conftest import make_settings

FILE_NAME = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №3.docx"


def _client(ocr=None, **overrides) -> TestClient:
    settings = make_settings(**overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_checker] = lambda: DocumentChecker(settings, ocr_provider=lambda s: ocr)
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_check_document_response_shape():
    data = build_docx(DocSpec(header_parts(), images=[funnel_image()]))
    client = _client(ocr=FakeOcr(funnel_tokens()))
    response = client.post(
        "/check-document",
        files={"file": ("upload.docx", data, "application/octet-stream")},
        data={"file_name": FILE_NAME},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "work_type", "routing", "issues", "extracted_data"}
    assert body["status"] == "passed"
    assert body["routing"] == "send_to_experts"
    assert body["work_type"] == "dz"
    assert set(body["extracted_data"]) == {"student_name", "business", "city", "package", "flow_number"}


def test_issue_shape():
    data = build_docx(DocSpec(header_parts(), landscape=True))
    response = _client().post("/check-document", files={"file": ("x.docx", data)}, data={"file_name": FILE_NAME})
    body = response.json()
    assert body["status"] == "needs_revision"
    assert body["routing"] == "return_to_student"
    assert body["issues"] == [
        {"code": "landscape_orientation", "area": "formatting", "description": body["issues"][0]["description"]}
    ]


def test_file_too_large():
    response = _client(max_file_size_mb=0.001).post(
        "/check-document", files={"file": ("x.docx", b"0" * 5000)}, data={"file_name": FILE_NAME}
    )
    assert response.status_code == 413


def test_missing_file_name_is_validation_error():
    response = _client().post("/check-document", files={"file": ("x.docx", b"123")})
    assert response.status_code == 422


def test_health():
    assert _client().get("/health").json()["status"] == "ok"


def test_api_key_required_when_configured():
    client = _client(api_key="secret-key")
    data = build_docx(DocSpec(header_parts()))
    request = {"files": {"file": ("x.docx", data)}, "data": {"file_name": FILE_NAME}}

    assert client.post("/check-document", **request).status_code == 401
    assert client.post("/check-document", headers={"X-API-Key": "wrong"}, **request).status_code == 401
    assert client.post("/check-document", headers={"X-API-Key": "secret-key"}, **request).status_code == 200
    # healthcheck Docker має працювати без ключа
    assert client.get("/health").status_code == 200
