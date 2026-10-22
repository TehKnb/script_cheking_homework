# DZ Checker — первинна перевірка навчальних робіт

REST API на FastAPI, яке перевіряє формальні критерії робіт (.doc / .docx) і вирішує,
куди їх направити: експертам, назад студенту або на ручну перевірку.

## Запуск

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # Windows (Linux/macOS: .venv/bin/pip)
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Документація OpenAPI: http://localhost:8000/docs

Зовнішні залежності:

| Що | Навіщо | Якщо немає |
|---|---|---|
| LibreOffice (`soffice`) | конвертація `.doc` → `.docx`, рендер у PDF (воронки SmartArt/фігурами) | `.doc` → `manual_review` (`conversion_unavailable`); SmartArt → `funnel_unreadable` |
| Tesseract (`ukr`+`eng`) **або** EasyOCR | OCR воронок-зображень | документи з зображеннями → `manual_review` (`funnel_check_unavailable`) |
| hunspell + словники `uk_UA`, `ru_RU`, `en_US` | орфографія | перевірка орфографії пропускається |

### Docker (рекомендовано)

Образ містить LibreOffice 7.4 (Writer, без GUI), Tesseract 5.3 (`ukr` + `eng`) і hunspell зі словниками
uk/ru/en, працює від непривілейованого користувача, має `HEALTHCHECK` на `/health`. Розмір ~1,2 ГБ.

```bash
cp .env.example .env          # і впишіть свій API_KEY
docker compose up -d --build
docker compose logs cloudflared | grep trycloudflare   # публічна адреса сервісу
```

Compose піднімає сервіс (локально лише на `127.0.0.1:8000`) і Cloudflare Tunnel, який дає публічну
HTTPS-адресу для n8n Cloud. Запити до `/check-document` потребують заголовка `X-API-Key`
зі значенням `API_KEY` з `.env`; `/health` відкритий.

* `COMPOSE_PROFILES=quick` — швидкий тунель `https://<випадкові-слова>.trycloudflare.com`. Безкоштовно,
  без акаунта, але **адреса змінюється після кожного перезапуску** контейнера/комп'ютера.
* `COMPOSE_PROFILES=named` — постійна адреса на вашому домені: Cloudflare Zero Trust → Networks → Tunnels →
  Create tunnel (Cloudflared) → скопіювати токен у `TUNNEL_TOKEN` → у Public Hostname вказати
  сервіс `http://dz-checker:8000`.

Комп'ютер із Docker має бути увімкнений, поки n8n надсилає роботи. Для цілодобової роботи — той самий
compose на VPS.

Без compose:

```bash
docker build -t dz-checker .
docker run -d --name dz-checker -p 8000:8000 dz-checker
```

EasyOCR замість Tesseract (образ більший на ~2 ГБ; при `OCR_ENGINE=auto` EasyOCR має пріоритет):

```bash
docker build -t dz-checker --build-arg INSTALL_EASYOCR=1 .
```

Кількість процесів uvicorn задає `WEB_CONCURRENCY` (за замовчуванням 2). Кожна конвертація `.doc`
запускає окремий LibreOffice з власним профілем, тому паралельні запити не конфліктують.
Орієнтовний час: у середньому ~3 с на роботу (конвертація `.doc`, рендер PDF для SmartArt, OCR зображень,
перевірка посилань); великі роботи з багатьма скріншотами — до 10–25 с.

## API

`POST /check-document` (multipart/form-data): `file` — файл, `file_name` — оригінальна назва.

```bash
curl -X POST localhost:8000/check-document \
  -F "file=@robota.doc" \
  -F "file_name=Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №3.doc"
```

```json
{
  "status": "needs_revision",
  "work_type": "dz",
  "routing": "return_to_student",
  "issues": [
    {"code": "wrong_currency", "area": "calculation",
     "description": "Усі фінансові показники та розрахунки мають бути в доларах ($). Знайдено суми не в доларах: «20 000 грн», …"}
  ],
  "extracted_data": {"student_name": "Іваненко Іван", "business": "Кав'ярня", "city": "Київ", "package": "ВІП", "flow_number": "67"}
}
```

`GET /health` — стан сервісу. Файл більший за `MAX_FILE_SIZE_MB` → HTTP 413.
Якщо задано `API_KEY`, без правильного заголовка `X-API-Key` → HTTP 401.

### Підключення до n8n Cloud (Telegram → сервіс)

1. **Credential**: Credentials → Create → **Header Auth**. Name: `X-API-Key`, Value: значення `API_KEY` з `.env`.
2. **Telegram Trigger**: Updates = `message`; Additional Fields → **Download Images/Files** = увімкнено
   (файл з'явиться в бінарному полі `data`). Студент надсилає роботу як файл; ліміт Telegram для ботів — 20 МБ.
3. *(необов'язково)* **IF**: `{{ $json.message.document }}` is not empty — щоб звичайний текст не йшов у сервіс.
4. **HTTP Request**:
   * Method `POST`, URL `https://<адреса-тунелю>/check-document`
   * Authentication: **Generic Credential Type** → **Header Auth** → credential з кроку 1
   * Send Body: так, Body Content Type: **Form-Data**
     * **n8n Binary File**: Name `file`, Input Data Field Name `data`
     * **Form Data**: Name `file_name`, Value `{{ $('Telegram Trigger').item.json.message.document.file_name }}`
   * Options → Timeout: `120000`
5. **Switch** за `{{ $json.routing }}`: `send_to_experts` / `return_to_student` / `manual_review`.
   Відповідь студенту (Telegram → Send Message):
   * Chat ID: `{{ $('Telegram Trigger').item.json.message.chat.id }}`
   * Text: `Робота потребує доопрацювання:\n{{ $json.issues.map(i => '• ' + i.description).join('\n') }}`

Після перезапуску швидкого тунелю оновіть URL у вузлі HTTP Request (або перейдіть на `named`).

### Фінальний статус

* є хоча б одна помилка «ручного» рівня (`unreadable_file`, `unclear_work_type`, `conversion_unavailable`,
  `funnel_unreadable`, `funnel_check_unavailable`, `short_content`, `processing_error`) → `manual_review` / `manual_review`;
* інакше є будь-які issues → `needs_revision` / `return_to_student`;
* issues немає → `passed` / `send_to_experts`.

## Що перевіряється

Правила зібрано з ТЗ, документа «Загальна інформація по ДЗ та гіпотезам» і відкалібровано на 88 реальних
роботах (прийнятих і відхилених, з текстами відмов). Повний каталог кодів — [app/issues.py](app/issues.py).

| Крок | Коди |
|---|---|
| Файл: лише `.doc`/`.docx` (формат визначається за вмістом); `.pdf`, `.pages`, `.rtf`, `.wps`, зображення не приймаються | `unreadable_file`, `unsupported_file_format`, `conversion_unavailable` |
| Тип роботи: назва файлу → перший рядок → зміст (т.А/ціна питання / «питання стратегічне») | `unclear_work_type`, `work_type_mismatch` |
| Шапка (назва файлу — `area: file_name`, перший рядок — `area: structure`) | `wrong_header_format`, `missing_student_name`, `missing_business`, `missing_city`, `missing_population`, `missing_package`, `missing_flow_number`, `missing_dz_number`, `missing_work_type_marker`, `forbidden_placeholder_word`, `uppercase_in_header`, `header_mismatch` |
| Оформлення | `wrong_font`, `wrong_first_line_size`, `wrong_body_text_size`, `wrong_bold_formatting`, `wrong_package_color`, `wrong_number_format`, `landscape_orientation`, `missing_space_after_punctuation`, `broken_characters`, `too_many_spelling_errors` |
| Валюта | `wrong_currency`, `missing_currency` |
| Розрахунки (усі типи робіт) | `wrong_calculation`, `point_a_parts_mismatch` |
| Гіпотеза | `missing_point_a`, `missing_point_b`, `missing_point_b_club`, `missing_growth_reason`, `missing_question_value`, `missing_hypothesis_description`, `missing_author_questions`, `wrong_question_value_calculation` |
| Стратегічне питання | `missing_point_a`, `missing_strategic_reason`, `missing_solution_options` (+ показники гіпотези для річного доступу) |
| ДЗ№2 / ДЗ№7 / усі ДЗ | `missing_point_a`, `missing_funnel` / `missing_landing_link` / `missing_material_link` |
| Посилання | `link_not_clickable`, `link_requires_access`, `link_not_working`, `landing_editor_link` |
| Воронки | `funnel_not_visual`, `missing_funnel_conversion`, `wrong_funnel_conversion`, `wrong_conversion_position`, `funnel_conversion_without_values`, `missing_funnel_stage_label`, `funnel_labels_inside`, `funnel_label_count_mismatch`, `empty_funnel_steps`, `wrong_funnel_order`, `small_text`, `funnel_unreadable`, `funnel_check_unavailable` |
| Мале наповнення — погодити з куратором | `short_content` |

### Правила, які варто знати

**Шапка**
* Пакет пишеться разом із номером потоку: `ВІП67`, `ВІП 67+`, `ВІП+ 66`, `Стандарт67`, `Річна63`, `Річна ВІП 58`
  (= ВІП+), `КМ+ 49`. Пакет і потік — жирні й одного кольору: Стандарт — синій (будь-який відтінок синього),
  ВІП/ВІП+ — червоний, Річна — чорний або червоний. У `extracted_data.package` — `Стандарт | ВІП | ВІП+ | Річна | КМ+`.
* «м. Київ» допускається; «пакет ВІП», «місто Київ», «67 потік» — `forbidden_placeholder_word`.
* Гіпотеза/Стратегічне питання з невеликого міста — після міста вказують населення: «м. Березівка (7 000), Одеська обл.».
  Обласні центри, великі міста та країни — без населення.
* Слова великими літерами в першому рядку (крім пакета) — `uppercase_in_header`.
* Назва файлу з підкресленнями (`Прізвище_Ім'я_…`) не розбирається на поля — у відмовах на це не зважають.
  Назва без формату взагалі («ДЗ 2.docx», «Гіпотеза.docx») — `wrong_header_format`.

**Оформлення**
* Шрифт і розмір — з урахуванням стилів і теми документа. Емодзі шрифтом не перевіряються; до 3% тексту
  іншим шрифтом допускається. Основний текст 12 pt (11 pt допускається), перший рядок 14 pt; таблиці не перевіряються.
* Жирним у першому рядку — лише бізнес і пакет з потоком.
* Тисячі: правильно `10 000`, `10,000`, `10.000`; помилка — `10000`. Як і у відмовах, перевіряється для Гіпотез і
  Стратегічних питань, для чисел від 10 000 (`NUMBER_FORMAT_*`). Роки, номери, телефони, дати не рахуються.
* Валюта — долари. `wrong_currency`, якщо сум у гривнях/у.о./€/zł/₽ більше двох і не менше, ніж у доларах
  (ДЗ№3 — найм, зарплати в гривнях — не перевіряється). Показники т.А/т.Б/ціна питання без валюти — `missing_currency`.
* Альбомна орієнтація хоча б однієї сторінки — `landscape_orientation`.
* Орфографія — hunspell (uk/ru/en) по словах з малої літери без маркетингового сленгу; помилка, якщо невідомих
  словникам слів ≥ 7% (`SPELLING_ERROR_SHARE`). Пропущені пробіли після розділових знаків — від 4 випадків.

**Гіпотеза** (за шаблоном курсу)

    Моя точка А (потік) – 3 000 $
    Точка Б (після базової 7-ми тижневої програми) – 5 000 $   ┐ обов'язково для річного доступу
    Точка Б (клуб) – 10 000 $                                  │ (Річна, ВІП+, КМ+)
    За рахунок чого відбулось зростання/падіння: …             ┘
    Планована т.Б – 13 000 $       (у потоці часто просто «Моя т.Б – …»)
    Ціна питання – 36 000 $

Ціна питання = (Планована т.Б − поточна точка) × 12, де поточна — остання фактична (т.Б клубу або т.А):
так рахують у прийнятих роботах. «Моя т.А - -2 100$» — від'ємна точка. Також потрібні текст гіпотези і питання автора.
Якщо в «Гіпотезі» написано «Моє питання стратегічне» — `work_type_mismatch`.

**Розрахунки** ([app/checkers/calculations.py](app/checkers/calculations.py)) — у всьому тексті роботи перевіряється
арифметика: `А = 693 × 3,90 = 2 700 $` (А = Кл × $), `8 556 + 1 000 + 267 = 9 823`, `ROI = (8 000 − 500) ÷ 500 × 100% = 1 500%`,
ланцюжки `70 × 30 000 = 2 100 000 / 8 = 262 500`, запис `8 000$ = 114 × 70$`. Дії `+ − × * x / ÷ :`, дужки, `%`
(«= 17%» порівнюється і як частка, і як відсотки). Округлення не є помилкою: кожне число має точність свого запису
(«0,3 × 49 000 = 16 333», де 0,3 — округлена 1/3), результат може бути округлений чи обрізаний до останнього знака
та ще 1% похибки → інакше `wrong_calculation`. Рядки з ціною питання перевіряє окреме правило Гіпотези.
Розкладання точки А: частини `А1`, `А2`, … (або `АI`, `АII`) після рядка з точкою А мають у сумі давати точку А
(допуск 5%) → `point_a_parts_mismatch`.

**Стратегічне питання**: точка А з валютою, «Моє питання стратегічне/важливе, оскільки…», варіанти вирішення;
для річного доступу — ще й показники гіпотези.

**ДЗ**: ДЗ№2 — точка А з сумою в $ і воронка (графікою; текстова — `funnel_not_visual`); ДЗ№7 — посилання на
опублікований лендинг (посилання на редактор `my.creatium.app`, Tilda, Wix… — `landing_editor_link`).
Якщо в роботі згадано Google Диск («креатив скинув через google диск»), а посилань немає зовсім —
`missing_material_link`. Обов'язкову воронку/креатив у ДЗ№4 не вимагаємо: у прийнятих роботах їх надсилали окремими файлами.

**Посилання** дозволені (Google Drive, Instagram, креативи, лендинги), але: URL з `http`/`www` має бути
гіперпосиланням; Google Drive/Docs перевіряються на доступ «для всіх, хто має посилання»; кабінет Ads Manager
і `fb.me/adspreview` — `link_requires_access`. Соцмережі на доступність не перевіряються (блокують роботів).

### Як розпізнаються воронки

Спільне ядро — [app/visual/layout.py](app/visual/layout.py): блоки (кроки) + текст з координатами.

* **SmartArt і фігури Word** — документ рендериться LibreOffice у PDF ([app/visual/pdf.py](app/visual/pdf.py)),
  звідти беруться зафарбовані фігури й слова з точними координатами (без OCR). Воронка — ≥ 3 блоки один під
  одним, вирівняні по центру, що помітно звужуються/розширюються (оргструктури з однакових блоків відсіюються).
* **Зображення** — OpenCV шукає блоки, а Tesseract читає **окремі фрагменти**: значення в центрі кожного блоку
  (режим «один рядок»), опис праворуч у смузі блоку, конверсію ліворуч на межі блоків (рамки текстових полів
  перед цим прибираються). Otsu застосовується лише до фрагмента, якщо без нього не вийшло чисте число
  (білі цифри на кольоровому блоці). Висновки — лише якщо прочитано ≥ 60% кроків. Якщо в документі поруч є
  текстові поля Word, «відсутні» на картинці описи/конверсії помилкою не вважаються (вони можуть бути поверх
  картинки). Нумерація «10, 9 … 1» у центрі сприймається як переплутаний порядок кроків, а не як значення.

  Чому так, а не «збільшити ×2 + сірий + Otsu для всього кадру»: на 8 реальних воронках (51 значення,
  21 конверсія, 51 опис) OCR усього кадру читав 6 / 8 / 28; збільшення ×2, сірий і глобальний Otsu
  результат погіршували (2–3 значення). Розпізнавання по фрагментах — 48 / 19 / 46 за ~0,8 с на зображення.
* **Текст/таблиця** з кроками і «N% від …» — `funnel_not_visual`.

Правила: у центрі — значення кроків (або нічого, якщо воронка гіпотетична), праворуч — опис кожного кроку,
ліворуч між блоками — конверсія `наступний / попередній × 100%`. Округлення і відкидання дробової частини
приймаються (похибка менша за одиницю останнього знака запису, для великих значень ще 1%). Перший перехід «Охоплення → Покази» (частота, >100%)
не перевіряється. Гіпотетична воронка (лише описи) допускається в ДЗ; у Гіпотезі/Стратегічному питанні воронка має
містити значення й конверсії. Дрібний текст у SmartArt/фігурах (помітно менший за основний) — `small_text`.

Ще кілька правил, знайдених на відхилених роботах:
* **Дрібний текст на схемі-зображенні** (воронка або оргструктура з ≥ 8 блоків) — висота слів на картинці × масштаб,
  у якому її показано в документі, < 4 pt → `small_text`. На схемах прийнятих робіт — від 7,7 pt, у відхиленій
  «через дрібний текст» — 2,6–3,5 pt. Скріншоти (кабінет реклами) не перевіряються: там дрібний текст приймають.
* **Описи у вузлах SmartArt-піраміди** (макети «Піраміда», «Перевернута піраміда», «Сегментована піраміда») Word
  показує всередині блоків → `funnel_labels_inside`. Читається з моделі SmartArt, бо LibreOffice такі піраміди
  іноді рендерить без тексту.
* **Опис під вершиною воронки** (або над верхнім блоком), для якого немає блоку → `funnel_label_count_mismatch`.
* **Розташування у воронках з фігур/SmartArt** (координати точні): описи — навпроти своїх блоків (зсув ≤ 0,45 висоти
  блоку) і рівно: або вздовж скосу воронки (до 60 pt від блоку, розкид відстаней ≤ 35 pt), або рівною колонкою
  (тоді позначаються лише ті, що з неї випадають) → `funnel_labels_misaligned`. Конверсії — ліворуч рівно на межі
  між блоками (зсув ≤ 0,3 висоти блоку), до 70 pt від воронки, розкид ≤ 30 pt → `wrong_conversion_position`.
  У прийнятих роботах: описи до 41 pt, розкид до 28 pt; конверсії до 0,19 висоти від межі й до 55 pt від воронки.
* **Конверсія — з точністю запису**: похибка менша за одиницю останнього знака, який написав студент
  («20%» для 19,6%, «72%» для 71,43%, «7,6%» для 7,64%; «8,5%» для 7,64% — помилка). Перший перехід
  «Охоплення → Покази»: без «%» — частота, не перевіряється; з «%» — рахується (240,9%, або частота «2,4%»).
* **Порожня воронка-картинка** (блоки без жодного напису), а конверсії дописані текстовими полями Word →
  `funnel_conversion_without_values`.

Що не перевіряється автоматично (лишається людині): читабельність скріншотів і помилки в тексті на зображеннях,
«сповзання» розмітки між версіями Word, змістовна якість/актуальність питання, вкладення окремими файлами
(воронка окремим документом, ексель — це рівень n8n).

## Налаштування (змінні оточення)

| Змінна | За замовчуванням | Опис |
|---|---|---|
| `API_KEY` | — | ключ для заголовка `X-API-Key`; порожній — авторизація вимкнена |
| `LIBREOFFICE_PATH` | автопошук | шлях до `soffice` |
| `CONVERSION_TIMEOUT_SEC` | 90 | тайм-аут конвертації |
| `MAX_FILE_SIZE_MB` | 25 | ліміт завантаження |
| `MAX_UNZIPPED_SIZE_MB` | 300 | захист від zip-бомб |
| `OCR_ENGINE` | `auto` | `auto` (EasyOCR, потім Tesseract), `easyocr`, `tesseract`, `none` |
| `OCR_LANGUAGES` | `uk,en` | мови OCR |
| `OCR_GPU` | false | GPU для EasyOCR |
| `OCR_MIN_CONFIDENCE` | 0.3 | поріг впевненості OCR |
| `TESSERACT_CMD` | — | шлях до `tesseract`, якщо не в PATH |
| `FUNNEL_CONVERSION_TOLERANCE_PP` | 0 | додатковий допуск конверсії, п.п. (базовий — одиниця останнього знака) |
| `REQUIRE_FLOW_NUMBER` | true | вимагати номер потоку |
| `DOCX_REQUIRES_FUNNEL` | false | правило ранньої версії ТЗ: `.docx` лише з воронкою (`wrong_file_format`) |
| `BODY_SIZE_TOLERANCE_PT` | 1 | наскільки основний текст може бути дрібнішим (11 pt) |
| `NUMBER_FORMAT_MIN_VALUE` / `NUMBER_FORMAT_WORK_TYPES` | 10000 / `hypothesis,strategic_question` | де вимагати роздільник тисяч |
| `CHECK_LINKS` / `LINK_TIMEOUT_SEC` / `MAX_LINKS_TO_CHECK` | true / 8 / 15 | перевірка доступності посилань |
| `CHECK_SPELLING` / `HUNSPELL_DICTIONARIES` / `SPELLING_ERROR_SHARE` | true / `uk_UA,ru_RU,en_US` / 0.07 | орфографія |
| `CHECK_SHORT_CONTENT` | true | роботи з малим наповненням — на погодження куратору |
| `RENDER_PDF` | true | рендер у PDF для воронок SmartArt/фігурами |
| `REQUIRED_FONT` / `FIRST_LINE_SIZE_PT` / `BODY_SIZE_PT` | Times New Roman / 14 / 12 | вимоги до оформлення |
| `QUESTION_VALUE_MULTIPLIER` / `QUESTION_VALUE_TOLERANCE` | 12 / 1 | формула ціни питання |

## Структура

```
app/
  main.py                  FastAPI, ендпоінти
  config.py                налаштування
  models.py                схема відповіді
  issues.py                каталог кодів помилок, серйозність
  services/
    pipeline.py            оркестрація перевірки та фінальний статус
    converter.py           визначення формату, LibreOffice
    document_loader.py     .docx -> абзаци/run-и/зображення/посилання
    styles.py              ефективні шрифт/розмір/жирний/колір з урахуванням стилів
  checkers/
    header.py              розбір шапки (назва файлу, перший рядок)
    classification.py      тип роботи
    formatting.py          оформлення
    content.py             валюта, пробіли після розділових знаків, биті символи, текстова воронка, обсяг
    structure.py           Гіпотеза, Стратегічне питання, ДЗ№2/№7, ціна питання
    links.py               посилання
    spelling.py            орфографія (hunspell)
    funnel.py              воронки та читабельність
  visual/
    layout.py              ядро розбору воронки за геометрією
    pdf.py                 векторні воронки й дрібний текст з PDF
    raster.py              воронки на зображеннях (OpenCV + OCR)
  ocr/
    engine.py              EasyOCR / Tesseract
tests/                     pytest (документи та воронки генеруються в тестах)
```

## Тести

```bash
.venv/Scripts/python -m pytest
```

Тести не потребують LibreOffice, OCR, мережі чи hunspell: конвертація підміняється, OCR замінено фейковим
рушієм із заданими координатами тексту, векторні воронки перевіряються на синтетичних сторінках PDF.
