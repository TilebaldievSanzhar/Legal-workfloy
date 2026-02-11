# Техническая спецификация проекта  
## Автоматизация реестра договоров (ETL Pipeline)

---

## 1. Обзор проекта

Необходимо разработать систему автоматизации (**ETL**), которая:

- мониторит заданные папки с договорами в облаке **Nextcloud** (включая вложенные подпапки),
- извлекает ключевые данные из **PDF-файлов** с помощью **LLM (Gemini)**,
- сохраняет результат в структурированную таблицу **Google Sheets**.

**Цель проекта:**

- исключить ручной ввод данных;
- обеспечить автоматический контроль сроков действия договоров;
- сохранять контекст (папка-источник);
- поддерживать актуальность реестра (учет удаленных файлов).

---

## 2. Технический стек

- **Язык:** Python 3.10+
- **Упаковка:** Docker / Docker Compose
- **LLM Provider:** Google Gemini API
- **Модель:** `gemini-2.0-flash`  
  *(или `gemini-1.5-flash` как fallback)*
- **Требование:** учет Rate Limits (RPM / TPM) при массовой обработке
- **Source (Источник):** Nextcloud (WebDAV / API)
- **Destination (Приемник):** Google Sheets API
- **Validation:** Pydantic (строгая типизация)

---

## 3. Архитектура проекта

```text
contract_automation/
├── config/
│   ├── settings.py              # Загрузка переменных окружения (.env)
│   └── google_credentials.json  # Service Account Key
├── src/
│   ├── __init__.py
│   ├── models.py                # Pydantic схемы и Enums
│   ├── llm_engine.py            # Логика Gemini + Rate Limiter
│   ├── services/
│   │   ├── nextcloud_client.py  # WebDAV клиент
│   │   └── gsheets_client.py    # Google Sheets клиент (append/update)
│   └── utils.py                 # Логирование
├── scripts/
│   ├── run_watcher.py           # Демон мониторинга (New & Deleted files)
│   ├── run_batch.py             # Историческая загрузка с троттлингом
├── .env
├── Dockerfile
├── requirements.txt
└── PROJECT_SPEC.md
````

---

## 4. Модели данных (`src/models.py`)

### 4.1. Статусы договоров (Enum)

Добавлен статус для файлов, удаленных из Nextcloud.

```python
from enum import Enum

class ContractStatus(str, Enum):
    ACTIVE = "действует"
    EXECUTED = "исполнен"
    EXPIRED = "истек"
    EXTENDED = "продлен"
    TERMINATED = "расторжение"
    CLAIM = "претензия"
    LITIGATION = "суд. разбирательство"
    DELETED_FROM_SOURCE = "удален из реестра"  # Файл удален из Nextcloud
    UNKNOWN = "требует проверки"
```

### 4.1.1. Типы документов (Enum)

```python
class DocumentType(str, Enum):
    CONTRACT = "договор"
    ADDENDUM = "доп соглашение"
    SPECIFICATION = "спецификация"
    ANNEX = "приложение"
    PROTOCOL = "протокол"
    ACT = "акт"
    INVOICE = "счёт-фактура"
    OTHER = "прочее"
```

---

### 4.2. Схема извлечения (LLM Output)

```python
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date

class ContractExtract(BaseModel):
    document_type: DocumentType = Field(description="Тип документа")
    contract_number: Optional[str] = Field(description="Номер договора")
    counterparty_name: str = Field(description="Наименование контрагента")
    city: Optional[str] = Field(
        description="Город из документа (Бишкек, Ош, Токмок и т.д.)"
    )
    start_date: Optional[date] = Field(description="Дата заключения")
    end_date: Optional[date] = Field(
        description="Дата окончания. Null если бессрочный"
    )
    is_perpetual: bool = Field(description="True если бессрочный")
    status: ContractStatus = Field(description="Статус на основе текста")
    summary: str = Field(description="Суть договора (до 10 слов)")
```

**Города Кыргызстана:** Бишкек, Ош, Токмок, Кант, Каракол, Джалал-Абад, Нарын, Талас, Баткен и др.
Поддерживаются форматы: "г. Бишкек", "г.Ош", "Бишкек"

---

### 4.3. Схема строки таблицы

```python
class TableRow(BaseModel):
    # LLM-извлечённые поля
    document_type: DocumentType
    contract_number: Optional[str]
    counterparty_name: str
    city: Optional[str]  # Приоритет: LLM > путь
    start_date: Optional[date]
    end_date: Optional[date]
    is_perpetual: bool
    status: ContractStatus
    summary: str

    # Из структуры папок
    subsidiary: str  # Дочерняя компания
    contract_category: str  # Категория (ДОГОВОРЫ ПОКУПАТЕЛИ 1410 и т.д.)
    year: Optional[int]  # Год из пути

    # Метаданные файла
    nextcloud_link: str
    filename: str
    source_folder: str
    file_id_hash: str
    processed_at: str
```

**Колонки Google Sheets:**
| Тип документа | Номер договора | Контрагент | Дочерняя компания | Категория | Город | Дата заключения | Дата окончания | Бессрочный | Статус | Суть договора | Ссылка Nextcloud | Имя файла | Папка-источник | file_id_hash | Дата обработки |

---

## 4.4. Структура папок и логика извлечения

**Возможные структуры папок:**

```text
Вариант 1 (типичный):
/Дочка/ДОГОВОРЫ/Год/Город/файлы.pdf

Вариант 2 (без городов):
/Дочка/ДОГОВОРЫ/Год/файлы.pdf

Вариант 3 (города внутри лет):
/Дочка/ДОГОВОРЫ/Год/Город/Спецификации/файлы.pdf
```

**Логика определения города:**
1. **Приоритет 1:** Извлечение из текста документа через LLM
   - Шапка документа ("г. Бишкек")
   - Адреса сторон
   - Место заключения договора
2. **Приоритет 2:** Из структуры папок (fallback)
   - Поиск известных городов в компонентах пути

**Логика определения года:**
- Извлекается из пути к файлу (папка с 4-значным числом 2000-2099)

**Логика определения типа документа:**
- Всегда извлекается через LLM из содержимого документа

---

## 5. Компоненты системы

### 5.1. Nextcloud Service

* Функция `scan_folder_recursive`
* Возвращает словарь:

  ```python
  {file_id: file_metadata}
  ```
* Используется для быстрого сравнения текущего состояния и сохраненного
  (**Set Difference**).

---

### 5.2. LLM Engine (с Rate Limiting)

* **Библиотека:** `google-genai`
* **Retry-механизм:** `tenacity`

**Обработка ошибок:**

* Обрабатывать `429 Resource Exhausted`
* Использовать **Exponential Backoff**:

  * 2с → 4с → 8с → ...

**Дополнительно:**

* В `run_batch.py` добавить принудительную паузу:

  ```python
  time.sleep(REQUEST_DELAY)
  ```
* Это предотвращает превышение лимитов API.

---

### 5.3. Google Sheets Service

**Метод:**

```text
update_status_by_file_id(file_id, new_status)
```

**Требования:**

* Находит строку по `file_id`:

  * скрытая колонка с `file_id`, или
  * поиск по ссылке на файл
* Меняет статус на **"удален из реестра"**
* ❗️Строка физически **не удаляется** — история сохраняется

---

## 6. Сценарии работы

### Сценарий 1: Мониторинг

`scripts/run_watcher.py`

**Инициализация:**

* Загрузка `processed_state.json` (состояние прошлого сканирования)

**Сканирование:**

* Получить текущий список файлов `current_files`
* Источник: `TARGET_FOLDERS`

**Анализ изменений:**

```text
new_ids      = current_ids - stored_ids
deleted_ids  = stored_ids - current_ids
```

**Обработка новых файлов:**

1. Скачать файл
2. Прогнать через LLM
3. Добавить строку в Google Sheets
4. Обновить `processed_state.json`

**Обработка удаленных файлов:**

* Для каждого `deleted_id`:

  ```python
  gsheets.update_status(
      deleted_id,
      ContractStatus.DELETED_FROM_SOURCE
  )
  ```
* Удалить запись из `processed_state.json`

---

### Сценарий 2: Историческая обработка

`scripts/run_batch.py`

Предназначен для первичной загрузки **тысяч файлов**.

**Троттлинг:**

* `Batch size = 10`
* После каждого файла:

  ```python
  time.sleep(2)
  ```
* После каждой пачки — увеличенная пауза
* При ошибке API:

  * пауза **60 секунд**
  * повтор запроса

**Дополнительно:**

* Вывод прогресс-бара в консоль (`tqdm`)

---

## 7. Переменные окружения (`.env`)

```env
# Nextcloud & Google Configs
NC_HOST=...
SPREADSHEET_ID=...

# Tuning
POLL_INTERVAL=600          # Интервал проверки (сек)
API_REQUEST_DELAY=2.0      # Пауза между запросами к LLM (сек)
BATCH_SIZE=5               # Размер пачки для записи в Sheets
```

---

## 8. Критерии успеха

* Удаление файла из Nextcloud приводит к смене статуса
  **"удален из реестра"** в Google Sheets при следующем цикле.
* При массовой обработке скрипт:

  * не падает на `429`,
  * корректно ждет и продолжает работу.
* Файлы корректно определяются на **всех уровнях вложенности** целевых папок.

---

## 9. Журнал изменений

### 9.1. Детекция переименования файлов (Watcher)

В `run_watcher.py` добавлена логика обнаружения переименованных файлов:

- При сканировании, если файл появляется как "новый" и одновременно "удалённый" файл имеет тот же размер и ту же родительскую папку — это считается переименованием.
- Вместо удаления старой строки и создания новой, обновляются метаданные существующей строки в Google Sheets:
  - Имя файла, ссылка Nextcloud, папка-источник, `file_id_hash`.
- Метод `update_row_metadata()` в `gsheets_client.py` обновляет колонки L-O за одну операцию.

### 9.2. Исправление извлечения дочерней компании (subsidiary)

Функция `extract_subsidiary()` в `path_parser.py` исправлена:

- Ранее извлекала корневую папку из пути (например, `Freigaben`).
- Теперь извлекает `parts[-1]` из TARGET_FOLDER — последний компонент целевой папки.
- Пример: для `/Freigaben/OASIS AGRO/ДОГОВОРЫ/ДОГОВОРЫ SCAN/ДОГОВОРЫ ПОКУПАТЕЛИ 1410/НА/` → subsidiary = `НА`.

### 9.3. Новая колонка "Категория" (`contract_category`)

Добавлена колонка **E — "Категория"** для различения типов договоров по родительской папке.

**Логика извлечения:**
- Из TARGET_FOLDER берётся `parts[-2]` (предпоследний компонент пути).
- Пример: `/Freigaben/OASIS AGRO/ДОГОВОРЫ/ДОГОВОРЫ SCAN/ДОГОВОРЫ ПОКУПАТЕЛИ 1410/НА/` → категория = `ДОГОВОРЫ ПОКУПАТЕЛИ 1410`.

**Затронутые файлы:**
- `src/path_parser.py` — новая функция `extract_contract_category()`, добавлено поле `contract_category` в `PathInfo`.
- `src/models.py` — новое поле `contract_category` в `TableRow`, обновлены `from_extract()`, `to_sheets_row()`, `get_headers()`.
- `src/services/gsheets_client.py` — все индексы колонок сдвинуты на +1 начиная с колонки E.
- `scripts/run_watcher.py`, `scripts/run_batch.py` — передают `contract_category` в `TableRow.from_extract()`.

### 9.4. Обновлённая структура колонок Google Sheets (A-P, 16 колонок)

| Колонка | Заголовок | 0-индекс | Поле |
|---------|-----------|----------|------|
| A | Тип документа | 0 | document_type |
| B | Номер договора | 1 | contract_number |
| C | Контрагент | 2 | counterparty_name |
| D | Дочерняя компания | 3 | subsidiary |
| **E** | **Категория** | **4** | **contract_category** |
| F | Город | 5 | city |
| G | Дата заключения | 6 | start_date |
| H | Дата окончания | 7 | end_date |
| I | Бессрочный | 8 | is_perpetual |
| J | Статус | 9 | status |
| K | Суть договора | 10 | summary |
| L | Ссылка Nextcloud | 11 | nextcloud_link |
| M | Имя файла | 12 | filename |
| N | Папка-источник | 13 | source_folder |
| O | file_id_hash | 14 | file_id_hash |
| P | Дата обработки | 15 | processed_at |

**Миграция:** После деплоя необходимо либо вставить пустую колонку E в существующие листы вручную, либо очистить листы и перезапустить `run_batch.py`.

