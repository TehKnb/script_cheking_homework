import io
import urllib.error

import pytest

from app.checkers import links
from tests.conftest import make_settings


class _FakeResponse(io.BytesIO):
    def __init__(self, url: str, body: str) -> None:
        super().__init__(body.encode("utf-8"))
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _doc(text: str, hyperlinks: tuple[str, ...] = ()):
    from types import SimpleNamespace

    return SimpleNamespace(full_text=text, hyperlinks=list(hyperlinks))


def test_address_without_scheme_is_a_link():
    _, urls = links.collect_links(_doc("Лендінг: opt.example.com.ua. Пошта info@site.com, сайт https://a.com.ua/x"))
    assert urls == ["https://a.com.ua/x", "opt.example.com.ua"]


def test_editor_without_scheme_is_found():
    from app.issues import IssueCollector

    issues = IssueCollector()
    doc = _doc("Лендінг: my.creatium.app/site/123")
    links.check_links(doc, make_settings(check_links=False), issues)
    assert issues.has("landing_editor_link") and issues.has("link_not_clickable")


def _serve(monkeypatch, *, body: str = "", final: str | None = None, status: int | None = None) -> None:
    def fake_urlopen(request, timeout):
        url = request.full_url
        if status is not None:
            raise urllib.error.HTTPError(url, status, "error", {}, None)
        return _FakeResponse(final or url, body)

    monkeypatch.setattr(links.urllib.request, "urlopen", fake_urlopen)


URL = "https://drive.google.com/file/d/abc/view"


def test_public_drive_page_with_sign_in_button_is_ok(monkeypatch):
    # Публічна сторінка Диска показує назву файлу і має кнопку «Увійти» (ServiceLogin)
    _serve(monkeypatch, body='<title>video.MOV - Google Диск</title><a href="/ServiceLogin">Увійти</a>')
    assert links._probe_one(URL, 5) == "ok"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 401},
        {"status": 403},
        {"final": "https://accounts.google.com/v3/signin/identifier"},
        {"body": "<title>Google Диск – Потрібен доступ</title>"},
    ],
)
def test_closed_drive_file_requires_access(monkeypatch, kwargs):
    _serve(monkeypatch, **kwargs)
    assert links._probe_one(URL, 5) == "restricted"


def test_deleted_drive_file_is_broken(monkeypatch):
    _serve(monkeypatch, status=404)
    assert links._probe_one(URL, 5) == "broken"
