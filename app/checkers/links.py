"""Перевірка посилань у роботі.

Посилання дозволені (Google Drive, Instagram, рекламні креативи, лендинги), але:
* мають бути клікабельними гіперпосиланнями (а не просто текстом «https://…»);
* мають відкриватися без запиту доступу (Google Drive — «доступ за посиланням»);
* мають вести на опублікований лендинг, а не на його редактор (типова помилка в ДЗ№7).
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlsplit

from app.config import Settings
from app.issues import IssueCollector
from app.services.document_loader import ParsedDocument
from app.utils.text import quote_list

logger = logging.getLogger(__name__)

_EXPLICIT_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>«»\"]+", re.IGNORECASE)

# Редактори конструкторів сайтів замість опублікованої сторінки
_EDITOR_PATTERNS = (
    re.compile(r"//my\.creatium\.(?:app|io)/", re.I),
    re.compile(r"creatium\.(?:app|io)/(?:editor|constructor|site|page)", re.I),
    re.compile(r"tilda\.(?:cc|ru)/(?:page|projects|zero)", re.I),
    re.compile(r"(?:editor|manage)\.wix\.com", re.I),
    re.compile(r"wix\.com/(?:dashboard|editor)", re.I),
    re.compile(r"webflow\.com/design", re.I),
    re.compile(r"canva\.com/design/[^/]+/edit", re.I),
    re.compile(r"taplink\.cc/(?:profile|admin)", re.I),
    re.compile(r"sites\.google\.com/.*/edit", re.I),
    re.compile(r"docs\.google\.com/forms/d/[^/]+/edit", re.I),
)
# Сторінки, які відкриваються лише власнику кабінету
_PRIVATE_PATTERNS = (
    re.compile(r"adsmanager\.facebook\.com", re.I),
    re.compile(r"business\.facebook\.com", re.I),
    re.compile(r"facebook\.com/adsmanager", re.I),
    re.compile(r"fb\.me/adspreview", re.I),
)
_GOOGLE_RE = re.compile(r"//(?:drive|docs)\.google\.com/", re.I)
# Текст сторінки «Потрібен доступ» (у <title> чи заголовку), а не будь-яке згадування входу
_ACCESS_REQUIRED_RE = re.compile(r"You need access|Потрібен доступ|Нужен доступ|Request access", re.I)
# Соцмережі віддають заглушку роботам — їх доступність автоматично не перевіряємо
_SKIP_HTTP_RE = re.compile(r"facebook\.com|fb\.me|instagram\.com|youtube\.com|youtu\.be|t\.me/|tiktok\.com", re.I)
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _key(url: str) -> str:
    url = unquote(url.strip()).lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.split("#")[0].rstrip("/")


def collect_links(doc: ParsedDocument) -> tuple[list[str], list[str]]:
    """(гіперпосилання, URL у тексті з явним http/www)."""
    hyperlinks = [h for h in doc.hyperlinks if h and not h.lower().startswith(("mailto:", "#", "tel:"))]
    text_urls = [m.group(0).rstrip(".,;:)»") for m in _EXPLICIT_URL_RE.finditer(doc.full_text)]
    return hyperlinks, text_urls


def check_links(doc: ParsedDocument, settings: Settings, issues: IssueCollector) -> list[str]:
    hyperlinks, text_urls = collect_links(doc)
    linked = {_key(h) for h in hyperlinks}

    not_clickable = [u for u in text_urls if not any(_key(u).startswith(k) or k.startswith(_key(u)) for k in linked)]
    if not_clickable:
        issues.add("link_not_clickable", f"Посилання без гіперпосилання: {quote_list(not_clickable)}")

    all_links = list(dict.fromkeys(hyperlinks + text_urls))
    editors = [u for u in all_links if any(p.search(u) for p in _EDITOR_PATTERNS)]
    if editors:
        issues.add("landing_editor_link", f"Знайдено: {quote_list(editors)}")

    private = [u for u in all_links if any(p.search(u) for p in _PRIVATE_PATTERNS)]
    if settings.check_links:
        candidates = [u for u in all_links if u not in private and u not in editors and not _SKIP_HTTP_RE.search(u)]
        restricted, broken = _probe(candidates[: settings.max_links_to_check], settings.link_timeout_sec)
        private += restricted
        if broken:
            issues.add("link_not_working", f"Не відкриваються: {quote_list(broken)}")
    if private:
        issues.add("link_requires_access", f"Потребують доступу: {quote_list(private)}")
    return all_links


def _probe(urls: list[str], timeout: float) -> tuple[list[str], list[str]]:
    if not urls:
        return [], []
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        results = list(pool.map(lambda u: (u, _probe_one(u, timeout)), urls))
    restricted = [u for u, state in results if state == "restricted"]
    broken = [u for u, state in results if state == "broken"]
    return restricted, broken


def _probe_one(url: str, timeout: float) -> str:
    """ok | restricted | broken | unknown (мережеві збої не вважаємо помилкою студента)."""
    full = url if re.match(r"^https?://", url, re.I) else "https://" + url
    request = urllib.request.Request(full, headers={"User-Agent": _USER_AGENT, "Accept-Language": "uk,en"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 — лише http(s)
            final = resp.geturl()
            if urlsplit(final).netloc.endswith("accounts.google.com"):
                return "restricted"
            if _GOOGLE_RE.search(full):
                # Кнопка «Увійти» (ServiceLogin) є і на публічних сторінках Диска — ознакою закритого доступу
                # вона не є. Закритий файл анонімно віддає 401/403 або редирект на accounts.google.com
                body = resp.read(200_000).decode("utf-8", "ignore")
                if _ACCESS_REQUIRED_RE.search(body):
                    return "restricted"
            return "ok"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403) and _GOOGLE_RE.search(full):
            return "restricted"
        if exc.code in (404, 410):
            return "broken"
        return "unknown"
    except urllib.error.URLError as exc:
        # DNS: домен не існує
        if "Name or service not known" in str(exc.reason) or "getaddrinfo failed" in str(exc.reason):
            return "broken"
        return "unknown"
    except Exception:
        logger.debug("Link probe failed for %s", url, exc_info=True)
        return "unknown"
