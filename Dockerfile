FROM python:3.11-slim-bookworm

# LibreOffice Writer — конвертація .doc -> .docx і рендер у PDF (воронки SmartArt/фігурами);
# Tesseract з українською та англійською — OCR воронок на зображеннях;
# hunspell зі словниками uk/ru/en — орфографія;
# шрифти потрібні LibreOffice для коректного рендеру (назви шрифтів у документі при цьому зберігаються)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        tesseract-ocr tesseract-ocr-ukr tesseract-ocr-eng \
        hunspell hunspell-uk hunspell-ru hunspell-en-us \
        fonts-liberation fonts-dejavu-core fonts-crosextra-carlito fonts-crosextra-caladea \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# INSTALL_EASYOCR=1 — додатково поставити EasyOCR (образ стає на ~2 ГБ більшим)
ARG INSTALL_EASYOCR=0
COPY requirements.txt requirements-easyocr.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_EASYOCR" = "1" ]; then pip install --no-cache-dir -r requirements-easyocr.txt; fi

COPY app ./app

# LibreOffice потребує записуваного HOME; профіль на кожну конвертацію створюється в /tmp
RUN useradd --create-home --uid 1000 checker
USER checker

ENV PYTHONUNBUFFERED=1 \
    LIBREOFFICE_PATH=/usr/bin/soffice \
    OCR_ENGINE=auto \
    OCR_LANGUAGES=uk,en \
    WEB_CONCURRENCY=2

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY}"]
