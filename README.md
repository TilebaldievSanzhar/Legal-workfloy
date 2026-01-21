# Contract Automation ETL Pipeline

Система автоматизации реестра договоров: мониторинг папок Nextcloud, извлечение данных из PDF через Gemini LLM, сохранение в Google Sheets.

---

## Содержание

1. [Требования](#требования)
2. [Установка](#установка)
3. [Настройка Google Cloud](#настройка-google-cloud)
4. [Настройка Gemini API](#настройка-gemini-api)
5. [Конфигурация .env](#конфигурация-env)
6. [Формат путей Nextcloud](#формат-путей-nextcloud)
7. [Запуск](#запуск)
8. [Docker](#docker)
9. [Структура Google Sheets](#структура-google-sheets)
10. [Troubleshooting](#troubleshooting)

---

## Требования

- Python 3.10+
- Доступ к Nextcloud с WebDAV
- Google Cloud Project с включенным Sheets API
- Google Gemini API ключ
- Docker (опционально)

---

## Установка

### Локальная установка

```bash
# Клонировать/скопировать проект
cd contract_automation

# Создать виртуальное окружение
python -m venv venv

# Активировать (Windows)
venv\Scripts\activate

# Активировать (Linux/Mac)
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt
```

### Структура проекта

```
contract_automation/
├── config/
│   ├── settings.py              # Конфигурация
│   └── google_credentials.json  # ← Сюда положить credentials
├── src/                         # Исходный код
├── scripts/
│   ├── run_watcher.py           # Демон мониторинга
│   └── run_batch.py             # Массовая обработка
├── data/
│   └── processed_state.json     # ← Создастся автоматически
├── .env                         # ← Создать из .env.example
└── .env.example
```

---

## Настройка Google Cloud

### Шаг 1: Создание проекта

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте новый проект или выберите существующий
3. Запомните ID проекта

### Шаг 2: Включение API

1. Перейдите в **APIs & Services** → **Library**
2. Найдите и включите:
   - **Google Sheets API**
   - **Google Drive API**

### Шаг 3: Создание Service Account

1. Перейдите в **APIs & Services** → **Credentials**
2. Нажмите **Create Credentials** → **Service Account**
3. Заполните:
   - Name: `contract-automation`
   - ID: `contract-automation`
4. Нажмите **Create and Continue**
5. Роль можно пропустить → **Done**

### Шаг 4: Создание ключа

1. Кликните на созданный Service Account
2. Перейдите во вкладку **Keys**
3. **Add Key** → **Create new key** → **JSON**
4. Скачанный файл переименуйте в `google_credentials.json`
5. Положите в папку `config/`

### Шаг 5: Доступ к таблице

1. Создайте новую Google Таблицу
2. Скопируйте ID таблицы из URL:
   ```
   https://docs.google.com/spreadsheets/d/[ВОТ_ЭТОТ_ID]/edit
   ```
3. Откройте доступ к таблице для Service Account:
   - Нажмите **Поделиться**
   - Добавьте email Service Account (формат: `xxx@project-id.iam.gserviceaccount.com`)
   - Права: **Редактор**

---

## Настройка Gemini API

1. Перейдите в [Google AI Studio](https://aistudio.google.com/)
2. Нажмите **Get API Key** → **Create API Key**
3. Выберите проект или создайте новый
4. Скопируйте ключ (формат: `AIza...`)

### Лимиты API (бесплатный тариф)

| Модель | RPM | TPM |
|--------|-----|-----|
| gemini-2.0-flash | 15 | 1,000,000 |
| gemini-1.5-flash | 15 | 1,000,000 |

> **Важно:** При превышении лимитов система автоматически делает паузу и повторяет запрос.

---

## Конфигурация .env

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

### Полный пример .env

```env
# ============================================
# NEXTCLOUD
# ============================================

# URL сервера Nextcloud (без trailing slash)
NC_HOST=https://cloud.mycompany.ru

# Учетные данные
NC_USER=contract_bot
NC_PASSWORD=SecurePassword123

# Папки для мониторинга (см. раздел ниже)
TARGET_FOLDERS=/ООО Альфа/Договоры,/ООО Бета/Договоры,/ООО Гамма/Договоры

# ============================================
# GOOGLE
# ============================================

# Путь к файлу credentials (относительно корня проекта)
GOOGLE_CREDENTIALS_PATH=config/google_credentials.json

# ID таблицы Google Sheets
SPREADSHEET_ID=1ABC123xyz_your_spreadsheet_id_here

# ============================================
# GEMINI LLM
# ============================================

# API ключ
GEMINI_API_KEY=AIzaSyD_your_api_key_here

# Модель (gemini-2.0-flash или gemini-1.5-flash)
GEMINI_MODEL=gemini-2.0-flash

# ============================================
# ПАРАМЕТРЫ ОБРАБОТКИ
# ============================================

# Интервал проверки новых файлов (секунды)
# 600 = 10 минут
POLL_INTERVAL=600

# Задержка между запросами к LLM (секунды)
# Рекомендуется 2-4 секунды для бесплатного тарифа
API_REQUEST_DELAY=2.0

# Размер пачки для записи в Google Sheets
BATCH_SIZE=5

# Путь к файлу состояния
STATE_FILE_PATH=data/processed_state.json
```

---

## Формат путей Nextcloud

### Базовые правила

1. **Пути начинаются с `/`** — это корень вашего Nextcloud
2. **Разделитель — запятая** без пробелов после неё
3. **Кириллица поддерживается**
4. **Пробелы в путях допустимы**

### Примеры TARGET_FOLDERS

#### Пример 1: Простая структура
```
Nextcloud/
├── Договоры Альфа/
├── Договоры Бета/
└── Договоры Гамма/
```
```env
TARGET_FOLDERS=/Договоры Альфа,/Договоры Бета,/Договоры Гамма
```

#### Пример 2: Вложенная структура
```
Nextcloud/
├── Компании/
│   ├── ООО Альфа/
│   │   └── Договоры/
│   └── ООО Бета/
│       └── Договоры/
└── Архив/
    └── Старые договоры/
```
```env
TARGET_FOLDERS=/Компании/ООО Альфа/Договоры,/Компании/ООО Бета/Договоры,/Архив/Старые договоры
```

#### Пример 3: Структура по годам
```
Nextcloud/
└── Юридический отдел/
    ├── 2023/
    │   └── Договоры/
    ├── 2024/
    │   └── Договоры/
    └── 2025/
        └── Договоры/
```
```env
TARGET_FOLDERS=/Юридический отдел/2023/Договоры,/Юридический отдел/2024/Договоры,/Юридический отдел/2025/Договоры
```

### Как узнать правильный путь?

1. **Через веб-интерфейс Nextcloud:**
   - Откройте нужную папку
   - Посмотрите на URL: `https://cloud.example.com/apps/files/?dir=/Путь/К/Папке`
   - Используйте часть после `dir=`

2. **Через WebDAV:**
   ```bash
   curl -u username:password "https://cloud.example.com/remote.php/dav/files/username/"
   ```

### Важные нюансы путей

| Правильно | Неправильно | Причина |
|-----------|-------------|---------|
| `/Договоры` | `Договоры` | Нет начального `/` |
| `/Папка 1,/Папка 2` | `/Папка 1, /Папка 2` | Пробел после запятой |
| `/Компания/Docs` | `/Компания/Docs/` | Trailing slash |

---

## Запуск

### Режим 1: Массовая загрузка (Batch)

Используйте для первоначальной обработки всех существующих файлов:

```bash
# Из корня проекта
python scripts/run_batch.py
```

**Что происходит:**
1. Сканируются все папки из `TARGET_FOLDERS`
2. Для каждого PDF вызывается Gemini API
3. Данные записываются в Google Sheets
4. Прогресс сохраняется в `processed_state.json`

**Вывод:**
```
2024-01-15 10:30:00 | INFO | Starting Batch Processor
2024-01-15 10:30:01 | INFO | Scanning all folders...
2024-01-15 10:30:05 | INFO | Found 150 PDF files
2024-01-15 10:30:05 | INFO | Already processed: 0 files
Processing: 100%|██████████| 150/150 [25:30<00:00, ok=148, err=2]
```

**Возобновление после сбоя:**
Скрипт автоматически пропускает уже обработанные файлы. Просто запустите повторно.

### Режим 2: Мониторинг (Watcher)

Используйте для постоянного отслеживания изменений:

```bash
python scripts/run_watcher.py
```

**Что происходит:**
1. Каждые `POLL_INTERVAL` секунд сканируются папки
2. Новые файлы → обрабатываются и добавляются в таблицу
3. Удалённые файлы → статус меняется на "удален из реестра"

**Вывод:**
```
2024-01-15 10:30:00 | INFO | Starting Contract Watcher
2024-01-15 10:30:00 | INFO | Monitoring 5 folders
2024-01-15 10:30:00 | INFO | Poll interval: 600 seconds
2024-01-15 10:30:05 | INFO | Found 2 new files, 1 deleted files
2024-01-15 10:30:10 | INFO | Successfully processed: contract_123.pdf
2024-01-15 10:30:15 | INFO | Marked as deleted: abc123def456
2024-01-15 10:30:15 | INFO | Waiting 600 seconds until next cycle...
```

**Остановка:** `Ctrl+C`

---

## Docker

### Сборка образа

```bash
docker-compose build
```

### Запуск Watcher (фоновый режим)

```bash
docker-compose up -d watcher
```

### Просмотр логов

```bash
docker-compose logs -f watcher
```

### Запуск Batch (однократно)

```bash
docker-compose --profile batch run --rm batch
```

### Остановка

```bash
docker-compose down
```

### Проверка статуса

```bash
docker-compose ps
```

### Volumes

| Путь в контейнере | Описание |
|-------------------|----------|
| `/app/config` | Конфигурация (read-only) |
| `/app/data` | Файл состояния |

---

## Структура Google Sheets

### Автоматическое создание листов

Система автоматически создаёт отдельные листы для каждого года:
- `2023` — договоры с годом в пути
- `2024` — договоры с годом в пути
- `Без года` — если год не определён

### Колонки таблицы

| # | Колонка | Источник | Описание |
|---|---------|----------|----------|
| A | Номер договора | LLM | Извлекается из текста PDF |
| B | Контрагент | LLM | Вторая сторона договора |
| C | Дочерняя компания | Путь | Корневая папка из TARGET_FOLDERS |
| D | Город | Путь | Если есть в пути файла |
| E | Дата заключения | LLM | Формат: YYYY-MM-DD |
| F | Дата окончания | LLM | Пусто если бессрочный |
| G | Бессрочный | LLM | "Да" или "Нет" |
| H | Статус | LLM | См. список статусов |
| I | Суть договора | LLM | Краткое описание (до 10 слов) |
| J | Ссылка Nextcloud | Генерация | Прямая ссылка на файл |
| K | Имя файла | Путь | Название PDF |
| L | Папка-источник | Путь | Полный путь к папке |
| M | file_id_hash | Генерация | Скрытая колонка для поиска |
| N | Дата обработки | Генерация | Когда обработан файл |

### Статусы договоров

| Статус | Описание |
|--------|----------|
| действует | Договор активен |
| исполнен | Обязательства выполнены |
| истек | Срок действия закончился |
| продлен | Договор пролонгирован |
| расторжение | Договор расторгнут |
| претензия | Есть претензия |
| суд. разбирательство | Судебный процесс |
| удален из реестра | Файл удалён из Nextcloud |
| требует проверки | Статус не определён |

---

## Troubleshooting

### Ошибка: "Credentials file not found"

```
FileNotFoundError: Credentials file not found: config/google_credentials.json
```

**Решение:** Убедитесь, что файл `google_credentials.json` лежит в папке `config/`

### Ошибка: "Permission denied" в Google Sheets

```
gspread.exceptions.APIError: 403 The caller does not have permission
```

**Решение:**
1. Откройте Google Таблицу
2. Нажмите "Поделиться"
3. Добавьте email Service Account с правами "Редактор"

### Ошибка: "429 Resource Exhausted"

```
RateLimitError: Rate limit exceeded
```

**Решение:** Система автоматически обрабатывает эту ошибку. Если ошибка повторяется:
1. Увеличьте `API_REQUEST_DELAY` до 4-5 секунд
2. Уменьшите `BATCH_SIZE` до 3

### Ошибка: "WebDAV connection failed"

```
WebDavException: Connection refused
```

**Решение:**
1. Проверьте `NC_HOST` — должен быть полный URL с `https://`
2. Проверьте учётные данные
3. Убедитесь, что WebDAV включён на сервере Nextcloud

### Файлы не находятся

**Проверьте:**
1. Путь начинается с `/`
2. Нет trailing slash в конце
3. Нет лишних пробелов после запятых в `TARGET_FOLDERS`
4. Кодировка файла `.env` — UTF-8

### Как сбросить состояние?

Удалите файл состояния:
```bash
rm data/processed_state.json
```
При следующем запуске все файлы будут обработаны заново.

### Как обработать только новые файлы?

Watcher автоматически обрабатывает только новые файлы. Если нужно переобработать всё:
1. Удалите `processed_state.json`
2. Очистите Google Таблицу
3. Запустите `run_batch.py`

---

## Мониторинг и логи

### Формат логов

```
TIMESTAMP | LEVEL | COMPONENT | MESSAGE
```

### Уровни логирования

- `INFO` — основные события
- `WARNING` — rate limits, повторные попытки
- `ERROR` — ошибки обработки файлов

### Ротация логов (Docker)

```yaml
# docker-compose.yml
services:
  watcher:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

---

## Поддержка

При возникновении проблем:
1. Проверьте раздел Troubleshooting
2. Посмотрите логи: `docker-compose logs watcher`
3. Убедитесь в корректности путей в `.env`
