# API спецификация — al-video-kensetsu

Самодостаточный документ для интеграции (в т.ч. через LLM). Базовый URL:
`http://<host>:3000` для основного Docker Compose, `http://<host>:5000` для локального сервера.

Примеры curl ниже используют локальный порт 5000; для основного Compose
замените его на 3000.

Расположение реализации: [карта проекта](PROJECT_STRUCTURE.md). Запуск и конфигурация: [разработка](DEVELOPMENT.md).

**Auth:** сейчас нет. Доступ открытый в пределах сети, где слушает сервис.

**CORS:** переменная `CORS_ORIGINS` (через запятую) влияет на CORS / Socket.IO.
Default включает localhost/127.0.0.1 на портах 3000 (Compose) и 5000 (`run.py`).
Для другого порта задайте соответствующий origin явно.

---

## Главный сценарий

1. `GET /api/presets` — выбрать `video_id` + `audio_id` (и проверить `available` у audio).
2. Запуск кодирования одним из способов:
   - загрузка: `POST /api/upload` с `file` / `files` + `video_id` / `audio_id` (+ `audio_downmix`);
   - с хранилища: `POST /api/internal/encode` с `path` / `paths` + те же поля.
3. `GET /api/jobs` или `GET /api/jobs/<id>` — опрос статуса.
4. `GET /api/jobs/<id>/download` — скачать результат при `output_available=true`.
   Или `POST /api/jobs/download` с `{ "job_ids": [...] }` — ZIP без сжатия (`ZIP_STORED`).
   Для копии в папку на сервере — `POST /api/jobs/<id>/save`.
5. `DELETE /api/jobs/<id>/delete` или `POST /api/jobs/delete` с `{ "job_ids": [...] }`.

Достаточно REST; WebSocket опционален для live-прогресса.

Один набор `video_id` / `audio_id` / `audio_downmix` применяется ко всей пачке. Воркер кодирует до `max_concurrent_encodes` заданий одновременно (по умолчанию **1**; см. `GET`/`PUT /api/settings`). Env `MAX_CONCURRENT_ENCODES` — fallback до первого сохранения в файле настроек.

---

## Модель: независимые слои пресетов

Четыре слоя YAML (`presets/<layer>/`), у каждого ровно один `default: true`:

| Слой | Каталог | В UI/API | Дефолт |
|------|---------|----------|--------|
| **video** | `presets/video/` | да (`video_id`) | `hevc_auto_level_rip` |
| **audio** | `presets/audio/` | да (`audio_id`) | `copy` |
| **subtitles** | `presets/subtitles/` | нет (внутренний) | `copy` |
| **data** | `presets/data/` | нет (внутренний) | `copy` |

Плюс **`audio_downmix`**: `none` (по умолчанию) или `stereo`.

### Video

| id | name | CRF | x265 preset |
|----|------|-----|-------------|
| `hevc_rip` | HEVC rip | 23 | slow |
| `hevc_auto_level_rip` | HEVC rip auto level | 23 | slow |
| `hevc_remux` | HEVC remux | 21 | slower |

Default — `hevc_auto_level_rip` (HEVC rip auto level). В нём отсутствует
`x265_params`: level/ref выбирает сам x265. У `hevc_rip` и `hevc_remux`
по-прежнему явно заданы `level=5.1:ref=6`.

### Audio

| id | name | Кодек |
|----|------|-------|
| `copy` | Copy | copy |
| `opus` | Opus | libopus 160k VBR |
| `aac` | AAC | aac 192k |
| `aac_fdk` | AAC FDK | libfdk_aac 192k |
| `none` | None | без аудио |

При `audio_id=none` resolve автоматически берёт `subtitles=none` и `data=none`.

### Subtitles / data (внутренние)

- `copy` — map + codec copy;
- `none` — без потока.

Поле `available` в ответе API (только audio):

- обычно `true`;
- `false` у `aac_fdk`, если в FFmpeg нет `libfdk_aac`.

Неизвестный `video_id` / `audio_id` → HTTP **400**.  
`audio_downmix=stereo` при `copy` / `none` → HTTP **400**.  
Запрос `aac_fdk` без поддержки → HTTP **400**.

Для custom-параметров формы: `preset_modified=true` (UI) → `video_id`/`audio_id` = `custom`.

### Stereo downmix

При `audio_downmix=stereo` и re-encode:

1. ffprobe всех аудиопотоков (`channels`);
2. для каждой выбранной дорожки (`audio_map` = `0:a?` или `0:a:N`):
   - `channels > 2` → `pan` ATSC LoRo без LFE;
   - stereo/mono → обычный `-map` без pan;
3. если probe не удался → задание **ERROR** (без глобального `-ac 2`).

---

## Эндпоинты

### `GET /health`

Проверка живости.

```bash
curl -s http://localhost:5000/health
```

---

### `GET /api/settings` / `PUT /api/settings`

Настройки приложения. Сейчас: лимит одновременных кодирований.

**GET 200:**

```json
{
  "max_concurrent_encodes": 1,
  "max_concurrent_encodes_runtime": 1,
  "max_concurrent_encodes_from_file": false,
  "min_concurrent_encodes": 1,
  "max_concurrent_encodes_limit": 16,
  "default_concurrent_encodes": 1
}
```

**PUT body:** `{ "max_concurrent_encodes": 2 }` (целое, 1–16).
Булевы значения и дробные JSON числа (включая `2.0`) дают **400** без изменения
настроек. Form принимает строковую запись целого числа.

Сохраняется в `settings/app_settings.json`, применяется к `FFmpegWorker` без рестарта (уже идущие encode не прерываются). Пока ключ не сохранён — fallback на env `MAX_CONCURRENT_ENCODES` (дефолт **1**).

```bash
curl -s http://localhost:5000/api/settings
curl -s -X PUT http://localhost:5000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"max_concurrent_encodes":2}'
```

Некорректное значение → **400** `{ "error": "..." }`.

---

### `GET /api/presets`

**Ответ 200:**

```json
{
  "videos": [
    {
      "id": "hevc_auto_level_rip",
      "name": "HEVC rip auto level",
      "default": true,
      "video_codec": "libx265",
      "preset": "slow",
      "crf": "23"
    }
  ],
  "audios": [
    {
      "id": "copy",
      "name": "Copy",
      "default": true,
      "codec": "copy",
      "available": true
    }
  ],
  "defaults": {
    "video_id": "hevc_auto_level_rip",
    "audio_id": "copy",
    "audio_downmix": "none"
  }
}
```

```bash
curl -s http://localhost:5000/api/presets
```

### `GET /api/presets/resolve`

Слитые параметры: `?video_id=hevc_remux&audio_id=opus`.

---

### `POST /api/upload`

`Content-Type: multipart/form-data`

Поля:

- `file` / `files` — видео (`.mp4`, `.mkv`);
- `video_id` — по умолчанию default из YAML (`hevc_auto_level_rip`);
- `audio_id` — по умолчанию `copy`;
- `audio_downmix` — `none` \| `stereo` (по умолчанию `none`).

```bash
curl -s -F "file=@/path/video.mkv" -F "video_id=hevc_remux" \
  -F "audio_id=opus" -F "audio_downmix=stereo" \
  http://localhost:5000/api/upload
```

Пачка: одни `video_id` / `audio_id` / `audio_downmix` на все файлы.  
Коды: **200** / **207** / **400**.

Параметры ресурсов (form и internal JSON): `cpu_threads` — целое 0–64,
`thread_queue_size` — целое 0–8192, `x265_pools` — целое 0–32.
Ноль означает автоматический выбор; дроби, bool и выход за диапазон дают **400**
до копирования/хеширования исходника.

#### Загрузка частями

Каждый multipart POST содержит `file` (часть данных), `filename` (имя MP4/MKV),
`file_id`, `chunk_number`, `total_chunks` и одинаковые параметры кодирования.

- `file_id`: 1–128 символов `A–Z`, `a–z`, `0–9`, `_`, `-`.
- `1 <= total_chunks <= 100000`, `0 <= chunk_number < total_chunks`.
  Отсутствующие/некорректные поля дают **400** до записи данных.
- `filename` сохраняется как безопасное имя без каталога, как при полной загрузке.
- Повторный ID с другим именем/количеством частей даёт **409**.
- Промежуточный успех: **200** с `completed: false`; отправка последней части
  запускает сборку. Если не хватает частей — **400**, принятые данные сохраняются.
  Для повтора отправьте отсутствующие части, затем снова последнюю.
- Сборка создаёт задание только после проверки комплекта. Ошибка не оставляет
  неполный итоговый файл; части удаляются после успешного создания задания.
- Лимит запроса и сумма частей одного файла ограничены `MAX_CONTENT_LENGTH`
  (16 GiB по умолчанию). Превышение возвращает JSON и **413**.

---

### `POST /api/internal/encode`

Разрешённые корни задаются через `INTERNAL_SOURCE_ROOTS` (через запятую).
Если переменная не задана, используется `/anilibria`. Явно пустая строка
отключает этот источник → **403**. Файлы хранилища копируются в uploads;
оригинал остаётся на месте.

JSON:

```json
{
  "path": "/anilibria/Show/ep.mkv",
  "video_id": "hevc_remux",
  "audio_id": "opus",
  "audio_downmix": "stereo"
}
```

Алиасы полей: `video` ↔ `video_id`, `audio` ↔ `audio_id`.

Пачка: `"paths": [...]` — ответ как у batch upload: `created` (число),
`jobs`, `errors`; **200** при полном успехе, **207** при частичном,
**400** если ни одно задание не создано. Ошибки сохраняют точный путь в `errors[].path`.
Поддерживается также form: `path` или повторяющееся поле `paths`.

Успешный single POST (**200**) подтверждает создание задания в очереди:

```json
{
  "created": true,
  "queued": true,
  "encoding_started": false,
  "message": "Задание создано и добавлено в очередь",
  "job": {
    "id": "2ce50252-10ed-4382-839b-5a3e96b3df16",
    "input_path": "/anilibria/Шоу [01]/Серия [01] .mkv",
    "filename": "Серия [01] .mkv",
    "status": "planned",
    "progress": 0.0,
    "output_available": false,
    "download_url": null,
    "output_size_bytes": null
  }
}
```

Здесь и в batch `Job` показан сокращённо. `input_path` каждого успешного задания
точно повторяет переданный `path` / элемент `paths`, включая Unicode, пробелы,
скобки и внешние кавычки. Нормализация для доступа к файлу не меняет это поле.
Временный рабочий исходник хранится отдельно в `Job.original_path` и не отдаётся
через API. `filename` содержит только имя файла. Сопоставляйте результаты
batch по `input_path`: одинаковые имена и порядок `jobs` не определяют источник.

Копирование и вычисление хеша происходят во время POST; само кодирование
выполняется в фоне. POST не ждёт FFmpeg или remux. В зависимости от скорости
воркера квитанция может уже содержать `in_progress` или конечный статус.
Готовность после POST проверяйте через `GET /api/jobs/<id>`.

```bash
curl -s -X POST http://localhost:5000/api/internal/encode \
  -H "Content-Type: application/json" \
  -d '{"path":"/anilibria/Show/ep.mkv","video_id":"hevc_rip","audio_id":"copy"}'
```

#### Идемпотентность: необязательный `request_id`

JSON и form принимают `request_id`: строка из 1–128 символов
`A–Z`, `a–z`, `0–9`, `.`, `_`, `-`. Отсутствующее поле / JSON `null`
сохраняет обычное поведение: каждый POST создаёт новые задания.
Клиент должен сохранить ID до отправки, например UUID.

ID резервируется в SQLite **до копирования**. Одновременные одинаковые запросы
имеют одного владельца. Готовая квитанция сохраняется с исходным HTTP-кодом;
повтор возвращает тот же JSON и те же job IDs без копирования, resolve пресетов,
создания или запуска заданий, в том числе после рестарта. В ответ добавляются
`request_id` и `receipt_url`, а в заданиях сохраняется `request_id`.
Это снимок ответа при приёме; актуальный прогресс и доступность результата
получают отдельно через GET задания.

Другие параметры с тем же ID → **409**. Отпечаток строится из переданных полей,
включая точные пути, их порядок и формат JSON/form, без самого `request_id`.
Порядок ключей JSON не учитывается; отсутствующее поле, явный default,
алиас поля, строка/число или другой порядок `paths` считаются разными запросами.
Изменение defaults, настроек или исходного файла после приёма не меняет квитанцию.
Для одного ID используйте тот же формат и параметры. Сохраняются также ошибки
валидации/файлов после резервирования ID и частичный **207**: исправленный источник
не запускается повторно с этим ID.

Квитанции не удаляются при удалении заданий и не имеют автоматического TTL.
Повтор после удаления вернёт прежние IDs; GET удалённого задания вернёт **404**.
Для намеренного нового кодирования нужен новый ID. Гарантии требуют сохранения
SQLite (`JOB_STORE_PATH`, по умолчанию `/app/settings/jobs.sqlite3`) вместе с томом
`settings`. Потеря этой базы означает потерю истории идемпотентности.
Приложение по-прежнему запускается одним серверным процессом.

### `GET /api/internal/encode/requests/<request_id>`

Read-only получение квитанции после потери ответа POST. Ничего не копирует,
не создаёт и не запускает. Готовая квитанция возвращается как исходный POST,
включая HTTP **200**, **207** или сохранённый код ошибки. Неизвестный ID → **404**
с `error`, без `request_id`. При отключённом внутреннем источнике → **403**,
как и POST.

Пока владелец принимает запрос, GET и повторный POST возвращают **202**
с `Retry-After: 2`:

```json
{
  "request_id": "altt-123",
  "request_status": "processing",
  "receipt_url": "/api/internal/encode/requests/altt-123",
  "job_ids": []
}
```

`job_ids` содержит уже сохранённые задания этого запроса и может быть неполным.
Опросите `receipt_url` позднее; HTTP 202 сам по себе не подтверждает создание
всей пачки. Для несовпадающего повтора всё равно возвращается **409**.

После перезапуска во время приёма или непредвиденного прерывания владельца
незавершённая квитанция получает `request_status: interrupted`. GET и одинаковый
POST возвращают **202** с известными `job_ids`, без `Retry-After`; автоматическое
продолжение отключено. Сопоставьте известные задания через GET по `input_path`
и разберите неопределённый результат вручную до отправки с новым ID.
SQLite и файловые операции не являются одной транзакцией: прерывание во время
копирования может оставить временный файл без задания. Такой запрос не исполняется
повторно по догадке. При обычном рестарте незавершённые задания становятся `error`
с причиной в `error_message`; финальные `done`, `error`, `cancelled` сохраняются.

---

### `GET /api/jobs` / `GET /api/jobs/<id>` / download / save / stop / delete

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/jobs` | Список заданий |
| GET | `/api/jobs/<id>` | Одно задание |
| GET | `/api/jobs/<id>/download` | Скачать один готовый файл |
| HEAD | `/api/jobs/<id>/download` | Проверить готовность и размер без скачивания видео |
| POST | `/api/jobs/download` | Пакетное скачивание ZIP |
| POST | `/api/jobs/<id>/save` | Сохранить копию готового результата в папку на сервере |
| DELETE | `/api/jobs/<id>/delete` | Удалить одно задание |
| POST | `/api/jobs/delete` | Пакетное удаление |
| POST | `/api/jobs/<id>/stop` | Остановить planned/in_progress |

GET одного задания возвращает JSON Job (**200**), неизвестный/удалённый ID →
**404**. Download / HEAD: **200** только для доступного финального файла при
`done`, **400** пока статус другой, **404** если задание или результат отсутствует
либо результат не является доступным обычным файлом. `Content-Length` соответствует
размеру файла. Удаление файла после GET может изменить доступность к моменту download.
Имя скачивания в `Content-Disposition` — `{stem}_[HEVC].mkv`, без суффикса
задания. Внутреннее имя на диске остаётся `{stem}_[HEVC]_{id[:8]}.mkv`:
задания с одинаковым исходным именем не перезаписывают результаты друг друга.

Stop и single delete ждут завершения рабочего потока (включая preflight/remux),
а также медиапроцесса. Если поток не завершился за 5 секунд либо процесс ещё жив,
ответ — **409**; исходник, результат и запись задания не удаляются. Повторите
действие после остановки. Если к моменту остановки результат уже финализирован,
сохраняется `done`, он не заменяется на `cancelled`.

#### `POST /api/jobs/download`

Тело: `{ "job_ids": ["uuid", ...] }`.

- В архив попадают только `status=done` с существующим `output_path`.
- Если файл кандидата отсутствует, недоступен или не является обычным файлом — **архив не создаётся**, ответ **409** с `missing[]` (`job_id`, `filename`, `path`).
- Успех: `application/zip`, `Content-Disposition: attachment; filename="kensetsu-{blake2b8}.zip"`.
  - `blake2b8` = 8 hex от `blake2b(digest_size=4)` по отсортированным `job_id` через `\n` (только те, что в архиве).
  - Сжатие: `ZIP_STORED` (без deflate).
  - Comment архива: `kensetsu batch; N files; job_ids: …`.
- Имена внутри ZIP: `job.get_download_filename()`, без суффикса задания.
  Совпадения без учёта регистра получают ` (2)`, ` (3)` перед `.mkv`;
  например `episode_[HEVC].mkv`, `episode_[HEVC] (2).mkv`.

```bash
curl -s -X POST http://localhost:5000/api/jobs/download \
  -H "Content-Type: application/json" \
  -d '{"job_ids":["id1","id2"]}' -o batch.zip
```

#### `POST /api/jobs/<id>/save`

Копирует доступный финальный результат `done` в папку сервера
с готовым именем `{stem}_[HEVC].mkv`. Исходный результат и `output_path` задания
не меняются. Удаление задания не удаляет сохранённую копию.

```json
{"directory":"/exports/season-01","overwrite":false,"create_directories":true}
```

`directory` — непустой абсолютный путь к папке. В Docker это путь внутри
контейнера, доступный на запись; для Unraid, например `/anilibria/encoded/season-01`.
Корни разрешаются через `INTERNAL_OUTPUT_ROOTS` (список через запятую,
default `/anilibria`); явно пустая строка отключает сохранение (**403**).
Путь после `realpath`, включая символические ссылки, должен оставаться внутри
разрешённого корня. Корни исходников `INTERNAL_SOURCE_ROOTS` на сохранение не влияют.

`create_directories` необязателен, по умолчанию `false`; принимается только JSON
bool. При `false` целевая папка должна существовать, иначе **404**. При `true`
создаётся всё недостающее дерево подкаталогов внутри уже существующего разрешённого
корня; сам корень не создаётся, его отсутствие даёт **404**. Неверные флаги,
неготовый или отсутствующий результат и путь вне корней не создают папки.
Если после создания подкаталогов копирование завершилось ошибкой, пустые папки
остаются: приложение не удаляет каталоги хранилища.

В UI опция «Создать недостающие папки» включена по умолчанию и передаёт `true`;
её можно выключить для сохранения только в существующую папку.

`overwrite` также необязателен, по умолчанию `false`; принимается только JSON bool.
Если файл с готовым именем уже есть, ответ **409**, копирования нет:

```json
{"error":"Файл уже существует. Перезаписать его?","code":"file_exists","path":"/exports/season-01/episode_[HEVC].mkv","filename":"episode_[HEVC].mkv"}
```

Клиент предлагает «Перезаписать» или «Отмена». Только после явного подтверждения
повторяет запрос к той же папке с прежним `create_directories` и `overwrite: true`;
отмена не отправляет запрос.
Этот флаг разрешает заменить обычный файл; совпадение с самим результатом,
символическая ссылка, каталог и другой специальный объект дают **409** без
`code: "file_exists"`, предлагать их перезапись нельзя.

Успех — **200**:

```json
{"saved":true,"path":"/exports/season-01/episode_[HEVC].mkv","filename":"episode_[HEVC].mkv","overwritten":false}
```

`overwritten` показывает, заменён ли существующий файл. Остальные ошибки:
**400** — некорректные JSON/поля, относительный путь, путь не к папке или
неготовое задание; **403** — путь вне разрешённых корней или недостаточно прав;
**404** — нет задания, итогового файла, разрешённого корня или целевой папки
при `create_directories: false`; **500** — ошибка копирования.

```bash
curl -s -X POST 'http://localhost:5000/api/jobs/<id>/save' \
  -H "Content-Type: application/json" \
  -d '{"directory":"/exports/season-01","create_directories":true}'
```

#### `POST /api/jobs/delete`

Тело: `{ "job_ids": ["uuid", ...] }`. Удаляет задания **любого** статуса (как single delete).

Ответ: `{ "deleted": [...], "errors": [...], "results": [...] }`.

| Код | Когда |
|-----|--------|
| **200** | Все удалены |
| **207** | Частичный успех |
| **400** | Пустой/некорректный `job_ids` или ни одно не удалено |

Правила очистки файлов те же, что у single delete (`original` не трогаем для internal / если ещё используется другим job).
При ещё работающем потоке ошибка этого задания включается в `errors`; код ответа
пачки остаётся **207** при частичном успехе и **400**, если ничего не удалено.

```bash
curl -s -X POST http://localhost:5000/api/jobs/delete \
  -H "Content-Type: application/json" \
  -d '{"job_ids":["id1","id2"]}'
```

---

### `GET /api/system-info`

В т.ч. `libfdk_aac`, версии FFmpeg / mkvmerge.
Если системный uptime недоступен, endpoint сохраняет HTTP **200** и остальные
сведения, возвращая `uptime: "Недоступно"`, `system_status: "warning"`.

---

## Модель Job

`input_path` хранится и в POST квитанции, и в последующих GET / Socket.IO.
У заданий из upload и старых записей без этого поля оно равно `null`;
восстановление не подставляет вместо него путь временной копии.

Статусы сохраняют существующие имена:

- `planned` — создано, ожидает начала;
- `in_progress` — обрабатывается, включая финальные remux/metadata шаги;
- `done` — финальный результат записан после необходимых remux/metadata шагов;
- `error` — выполнение завершилось ошибкой, причина в `error_message`;
- `cancelled` — задание остановлено.

`progress` — проценты 0–100. Даже 100% FFmpeg не означает завершения remux.
`created_at`, `started_at`, `completed_at` — UTC ISO 8601 с `Z`;
отсутствующие даты — `null`. `completed_at` бывает также у ошибки и отмены,
поэтому успех определяется `status=done`.

| Поле | Описание |
|------|----------|
| `id` | ID задания для GET / download / save / stop / delete |
| `input_path` | Точная строка из internal `path` / `paths`, иначе `null` |
| `request_id` | ID запроса internal encode при его передаче, иначе `null` |
| `filename` | Имя входного файла без каталога |
| `output_filename` | Готовое имя `{stem}_[HEVC].mkv` без суффикса задания, для скачивания и серверной копии |
| `status`, `progress` | Состояние и проценты выполнения |
| `error_message` | Причина ошибки/отмены, иначе `null` |
| `created_at`, `started_at`, `completed_at` | Даты UTC с `Z` / `null` |
| `output_available` | `true` только при `done` и доступном обычном итоговом файле |
| `download_url` | Относительный `/api/jobs/<id>/download` при доступном результате, иначе `null` |
| `output_size_bytes` | Размер доступного файла, иначе `null` |
| `video_id` | id видео-пресета или `custom` |
| `audio_id` | id аудио-пресета или `custom` |
| `audio_downmix` | `none` \| `stereo` |
| `preset_label` | напр. `HEVC remux / Opus · stereo downmix` |
| `ffmpeg_params` | словарь параметров FFmpeg |

Subtitles/data ids в Job API не отдаются (подставляются при resolve).

Доступность вычисляется при сериализации, через stat и проверку доступа;
видео не читается и не хешируется. После удаления результата история сохраняет
`status: done`, но `output_available: false`, `download_url: null` и
`output_size_bytes: null`. История заданий восстанавливается из SQLite при старте;
само наличие MKV в outputs не создаёт задание.
Для восстановленных заданий GET Job и фактическое скачивание также используют
готовое имя без суффикса; `output_path` на диске не переименовывается.
Опубликованные квитанции internal encode неизменяемы: историческая квитанция
может по-прежнему содержать прежний `output_filename` с суффиксом задания.

---

## Ошибки

| Код | Когда |
|-----|--------|
| **400** | Неизвестный `video_id`/`audio_id`; stereo+copy/none; FDK недоступен; нет файлов/путей; batch delete без успехов |
| **202** | Квитанция internal encode ещё `processing` или `interrupted` |
| **207** | Batch: частичный успех (upload / internal encode / delete) |
| **403** | Internal encode / серверное сохранение выключены; путь вне соответствующих roots; недостаточно прав на сохранение |
| **404** | Задание / файл не найден |
| **409** | request_id повторён с другими параметрами; конфликт chunk file_id; поток/процесс ещё останавливается; batch download: недоступные результаты (`missing[]`); серверное сохранение: целевое имя занято или совпадает с самим результатом |
| **413** | Превышен размер запроса или суммарный размер частей файла |
| **500** | Внутренняя ошибка |

---

## Рекомендуемый flow

```text
GET  /api/presets
  → выбрать video_id + audio_id (available=true)
POST /api/upload  (files + video_id + audio_id [+ audio_downmix])
  или POST /api/internal/encode (paths + …)
GET  /api/jobs/<id>
GET  /api/jobs/<id>/download
  или POST /api/jobs/download  { job_ids }
  или POST /api/jobs/<id>/save  { directory, overwrite: false, create_directories: true }
       → при 409 + code=file_exists запросить подтверждение
       → при согласии повторить с overwrite: true
POST /api/jobs/delete  { job_ids }   # опционально, пакетно
```
