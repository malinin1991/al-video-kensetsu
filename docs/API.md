# API спецификация — al_web_video_encoder

Самодостаточный документ для интеграции (в т.ч. через LLM). Базовый URL по умолчанию: `http://<host>:5000`.

**Auth:** сейчас нет. Доступ открытый в пределах сети, где слушает сервис.

**CORS:** переменная `CORS_ORIGINS` (через запятую) влияет на CORS / Socket.IO.

---

## Главный сценарий

1. `GET /api/presets` — выбрать `preset_id` (и проверить `available`).
2. Запуск кодирования одним из способов:
   - загрузка: `POST /api/upload` с `file` / `files` + `preset_id`;
   - с хранилища: `POST /api/internal/encode` с `path` / `paths` + `preset_id`.
3. `GET /api/jobs` или `GET /api/jobs/<id>` — опрос статуса.
4. `GET /api/jobs/<id>/download` — скачать результат при `status=done`.

Достаточно REST; WebSocket опционален для live-прогресса.

Один `preset_id` применяется ко всей пачке. Очередь воркера обрабатывает задания по одному.

---

## Пресеты (`preset_id`)

Группы: `rip` (WEBRip/HDTV/BDRip) и `remux` (BD/ remux, CRF 21, slower).

**Клиентам следует всегда передавать уникальный `preset_id`** (не display name): у rip/remux совпадают имена («По умолчанию», «HEVC + AAC» и т.д.). Поле `preset` (имя) допускается только если имя однозначно; при нескольких совпадениях — HTTP **400** со списком id.

| id | group | name | Назначение |
|----|-------|------|------------|
| `default` | rip | По умолчанию | CRF 23 / slow, аудио copy |
| `aac_audio` | rip | HEVC + AAC | AAC 192k |
| `aac_fdk_audio` | rip | HEVC + AAC FDK | `libfdk_aac` 192k |
| `opus_audio` | rip | HEVC + Opus | Opus VBR |
| `video_only` | rip | Только видео | без аудио/субтитров/данных |
| `bd_default` | remux | По умолчанию | CRF 21 / slower, аудио copy |
| `bd_aac_audio` | remux | HEVC + AAC | AAC 192k |
| `bd_aac_fdk_audio` | remux | HEVC + AAC FDK | `libfdk_aac` 192k |
| `bd_opus_audio` | remux | HEVC + Opus | Opus VBR |
| `bd_video_only` | remux | Только видео | без аудио/субтитров/данных |

Поле `available` в ответе API:

- обычно `true`;
- `false` у FDK-пресетов, если в FFmpeg нет энкодера `libfdk_aac`.

Неизвестный `preset_id` → HTTP **400**.  
Неоднозначное имя пресета → HTTP **400** (текст ошибки перечисляет совпавшие id).  
Запрос FDK-пресета без поддержки → HTTP **400**.

Для custom-параметров формы: `preset_id=custom` или `preset_modified=true` (UI). Для интеграций предпочтителен стабильный `preset_id` без полного набора полей.

---

## Эндпоинты

### `GET /health`

Проверка живости.

**Ответ 200:** JSON со статусом здоровья (как реализовано в приложении).

```bash
curl -s http://localhost:5000/health
```

---

### `GET /api/presets`

Список пресетов (отсортирован: `rip`, затем `remux`; внутри группы default/bd_default первыми).

**Ответ 200:** массив объектов:

```json
[
  {
    "id": "default",
    "name": "По умолчанию",
    "group": "rip",
    "description": "...",
    "available": true,
    "video_codec": "libx265",
    "preset": "slow",
    "crf": "23",
    "audio_settings": { "codec": "copy", "bitrate": null, "sample_rate": null, "vbr": null }
  }
]
```

```bash
curl -s http://localhost:5000/api/presets
```

---

### `GET /api/presets/<preset_id>`

Один пресет. **404**, если нет.

---

### `POST /api/upload`

`Content-Type: multipart/form-data`

#### Один файл (обратная совместимость)

Поля:

- `file` — видео (`.mp4`, `.mkv`);
- `preset_id` — id пресета (рекомендуется).

**Ответ 200:**

```json
{
  "message": "Файл загружен успешно",
  "job": { "...": "Job.to_dict()" },
  "completed": true
}
```

```bash
curl -s -F "file=@/path/video.mkv" -F "preset_id=default" \
  http://localhost:5000/api/upload
```

#### Пачка

Поля:

- `files` — несколько частей с одним именем `files` (и/или несколько `file`);
- `preset_id` — один на всю пачку.

**Ответ:**

```json
{
  "created": 3,
  "message": "Создано заданий: 3",
  "jobs": [ { "...": "Job" } ],
  "errors": [ { "filename": "bad.xyz", "error": "..." } ]
}
```

Коды:

- **200** — все файлы приняты;
- **207** — частичный успех (`jobs` и `errors` непусты);
- **400** — ни одного задания / ошибка валидации `preset_id`/FDK (до создания jobs).

Чанковая загрузка (`chunk_number`, `total_chunks`, `file_id`) — по одному файлу, как раньше.

```bash
curl -s -F "files=@a.mkv" -F "files=@b.mkv" -F "preset_id=bd_opus_audio" \
  http://localhost:5000/api/upload
```

---

### `POST /api/internal/encode`

Кодирование файла(ов) с примонтированного хранилища.  
Если `INTERNAL_SOURCE_ROOTS` не задан → **403**.

`Content-Type: application/json` или `multipart/form-data` / `application/x-www-form-urlencoded`.

#### Один путь

JSON:

```json
{ "path": "/anilibria/Show/ep.mkv", "preset_id": "bd_opus_audio" }
```

**Ответ 200:**

```json
{
  "created": true,
  "queued": true,
  "encoding_started": false,
  "message": "Задание создано и добавлено в очередь",
  "job": { "...": "Job" }
}
```

```bash
curl -s -X POST http://localhost:5000/api/internal/encode \
  -H "Content-Type: application/json" \
  -d '{"path":"/anilibria/Show/ep.mkv","preset_id":"default"}'
```

#### Пачка

JSON:

```json
{
  "paths": ["/anilibria/a.mkv", "/anilibria/b.mkv"],
  "preset_id": "bd_opus_audio"
}
```

Form: повторяющееся поле `paths` + `preset_id`.

Ответ как у batch upload: `created` / `jobs` / `errors`, коды **200** / **207** / **400**.  
Ошибка `preset_id`/FDK — до создания заданий (весь запрос **400**).

```bash
curl -s -X POST http://localhost:5000/api/internal/encode \
  -H "Content-Type: application/json" \
  -d '{"paths":["/anilibria/a.mkv","/anilibria/b.mkv"],"preset_id":"bd_default"}'
```

---

### `GET /api/jobs`

Список всех заданий (массив `Job`).

### `GET /api/jobs/<id>`

Одно задание. **404**, если нет.

### `GET /api/jobs/<id>/download`

Скачивание результата. **404**, если нет файла / задания.

### `POST /api/jobs/<id>/stop`

Остановка кодирования.

### `DELETE /api/jobs/<id>/delete`

Удаление задания (и остановка процесса при необходимости).

---

### `GET /api/settings/metadata` / `PUT /api/settings/metadata`

Ник и флаги обновления метаданных (см. реализацию в `app/routes.py`).

---

### `GET /api/logs`

In-memory логи.

Query:

- без параметров — системный поток;
- `job_id=<uuid>` — логи конкретного задания (**404**, если задания нет);
- `stream=job` без `job_id` → **400**.

```bash
curl -s http://localhost:5000/api/logs
curl -s "http://localhost:5000/api/logs?job_id=<uuid>"
```

### `GET /api/logs/jobs`

Список заданий, у которых есть логи: `[{ "id", "label" }, …]` (только существующие jobs).

### `POST /api/logs/clear`

Очистка:

- `?stream=system` (по умолчанию) — системный поток;
- `?job_id=<uuid>` — логи задания.

---

### `GET /api/system-info`

Версии и флаги окружения.

Важные поля:

- `ffmpeg_version` — короткий токен версии без префикса `ffmpeg ` (например `n9.0.1-6-g…`);
- `ffmpeg_version_label` — полная метка для UI (`ffmpeg n9.0.1-…`);
- `ffmpeg_configuration` — строка configuration;
- `ffmpeg_libraries` — объект `libavcodec` / `libavformat` / …;
- `libfdk_aac` — boolean;
- `mkvmerge_status` / `mkvmerge_version`;
- CPU / memory / app build.

```bash
curl -s http://localhost:5000/api/system-info
```

---

## Модель Job (кратко)

Типичные поля `to_dict()`:

| Поле | Описание |
|------|----------|
| `id` | UUID задания |
| `filename` | Исходное имя |
| `status` | `planned` \| `in_progress` \| `done` \| `error` \| `cancelled` |
| `progress` | 0–100 |
| `preset_id` | id пресета или `custom` |
| `original_path` | путь исходника в uploads |
| `output_path` | путь результата (после encode) |
| `error_message` | текст ошибки |
| `created_at` / `completed_at` | ISO timestamps (UTC) |
| `ffmpeg_params` | словарь параметров FFmpeg |

---

## Ошибки

| Код | Когда |
|-----|--------|
| **400** | Неизвестный / неоднозначный `preset_id`; FDK недоступен; нет файлов/путей; неверный тип файла (single) |
| **207** | Batch: часть файлов/путей принята, часть с ошибками |
| **403** | Internal encode выключен; путь вне `INTERNAL_SOURCE_ROOTS` |
| **404** | Задание / пресет / файл не найден |
| **500** | Внутренняя ошибка |

Тело ошибки обычно: `{ "error": "текст" }`.

---

## WebSocket (кратко)

События: `job_update`, `progress_update`, `log_update`.  
Для интеграций достаточно polling REST (`GET /api/jobs/<id>`).

---

## Рекомендуемый flow

```text
GET  /api/presets
  → выбрать preset_id с available=true
POST /api/upload  (files + preset_id)
  или POST /api/internal/encode (paths + preset_id)
GET  /api/jobs/<id>  (пока status не done/error/cancelled)
GET  /api/jobs/<id>/download
```
