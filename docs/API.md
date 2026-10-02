# API спецификация — al_web_video_encoder

Самодостаточный документ для интеграции (в т.ч. через LLM). Базовый URL по умолчанию: `http://<host>:5000`.

**Auth:** сейчас нет. Доступ открытый в пределах сети, где слушает сервис.

**CORS:** переменная `CORS_ORIGINS` (через запятую) влияет на CORS / Socket.IO.

---

## Главный сценарий

1. `GET /api/presets` — выбрать `video_id` + `audio_id` (и проверить `available` у audio).
2. Запуск кодирования одним из способов:
   - загрузка: `POST /api/upload` с `file` / `files` + `video_id` / `audio_id` (+ `audio_downmix`);
   - с хранилища: `POST /api/internal/encode` с `path` / `paths` + те же поля.
3. `GET /api/jobs` или `GET /api/jobs/<id>` — опрос статуса.
4. `GET /api/jobs/<id>/download` — скачать результат при `status=done`.
   Или `POST /api/jobs/download` с `{ "job_ids": [...] }` — ZIP без сжатия (`ZIP_STORED`).
5. `DELETE /api/jobs/<id>/delete` или `POST /api/jobs/delete` с `{ "job_ids": [...] }`.

Достаточно REST; WebSocket опционален для live-прогресса.

Один набор `video_id` / `audio_id` / `audio_downmix` применяется ко всей пачке. Воркер кодирует до `max_concurrent_encodes` заданий одновременно (по умолчанию **1**; см. `GET`/`PUT /api/settings`). Env `MAX_CONCURRENT_ENCODES` — fallback до первого сохранения в файле настроек.

---

## Модель: независимые слои пресетов

Четыре слоя YAML (`presets/<layer>/`), у каждого ровно один `default: true`:

| Слой | Каталог | В UI/API | Дефолт |
|------|---------|----------|--------|
| **video** | `presets/video/` | да (`video_id`) | `hevc_rip` |
| **audio** | `presets/audio/` | да (`audio_id`) | `copy` |
| **subtitles** | `presets/subtitles/` | нет (внутренний) | `copy` |
| **data** | `presets/data/` | нет (внутренний) | `copy` |

Плюс **`audio_downmix`**: `none` (по умолчанию) или `stereo`.

### Video

| id | name | CRF | x265 preset |
|----|------|-----|-------------|
| `hevc_rip` | HEVC rip | 23 | slow |
| `hevc_remux` | HEVC remux | 21 | slower |

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
      "id": "hevc_rip",
      "name": "HEVC rip",
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
    "video_id": "hevc_rip",
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
- `video_id` — по умолчанию default из YAML (`hevc_rip`);
- `audio_id` — по умолчанию `copy`;
- `audio_downmix` — `none` \| `stereo` (по умолчанию `none`).

```bash
curl -s -F "file=@/path/video.mkv" -F "video_id=hevc_remux" \
  -F "audio_id=opus" -F "audio_downmix=stereo" \
  http://localhost:5000/api/upload
```

Пачка: одни `video_id` / `audio_id` / `audio_downmix` на все файлы.  
Коды: **200** / **207** / **400**.

---

### `POST /api/internal/encode`

Если `INTERNAL_SOURCE_ROOTS` не задан → **403**.

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

Пачка: `"paths": [...]` — ответ как у batch upload.

```bash
curl -s -X POST http://localhost:5000/api/internal/encode \
  -H "Content-Type: application/json" \
  -d '{"path":"/anilibria/Show/ep.mkv","video_id":"hevc_rip","audio_id":"copy"}'
```

---

### `GET /api/jobs` / `GET /api/jobs/<id>` / download / stop / delete

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/jobs` | Список заданий |
| GET | `/api/jobs/<id>` | Одно задание |
| GET | `/api/jobs/<id>/download` | Скачать один готовый файл |
| POST | `/api/jobs/download` | Пакетное скачивание ZIP |
| DELETE | `/api/jobs/<id>/delete` | Удалить одно задание |
| POST | `/api/jobs/delete` | Пакетное удаление |
| POST | `/api/jobs/<id>/stop` | Остановить planned/in_progress |

#### `POST /api/jobs/download`

Тело: `{ "job_ids": ["uuid", ...] }`.

- В архив попадают только `status=done` с существующим `output_path`.
- Если у кандидата файл **нет на диске** — **архив не создаётся**, ответ **409** с `missing[]` (`job_id`, `filename`, `path`).
- Успех: `application/zip`, `Content-Disposition: attachment; filename="kensetsu-{blake2b8}.zip"`.
  - `blake2b8` = 8 hex от `blake2b(digest_size=4)` по отсортированным `job_id` через `\n` (только те, что в архиве).
  - Сжатие: `ZIP_STORED` (без deflate).
  - Comment архива: `kensetsu batch; N files; job_ids: …`.
- Имена внутри ZIP: `job.get_output_filename()`; при коллизии — суффикс `job_id[:8]`.

```bash
curl -s -X POST http://localhost:5000/api/jobs/download \
  -H "Content-Type: application/json" \
  -d '{"job_ids":["id1","id2"]}' -o batch.zip
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

```bash
curl -s -X POST http://localhost:5000/api/jobs/delete \
  -H "Content-Type: application/json" \
  -d '{"job_ids":["id1","id2"]}'
```

---

### `GET /api/system-info`

В т.ч. `libfdk_aac`, версии FFmpeg / mkvmerge.

---

## Модель Job (кратко)

| Поле | Описание |
|------|----------|
| `video_id` | id видео-пресета или `custom` |
| `audio_id` | id аудио-пресета или `custom` |
| `audio_downmix` | `none` \| `stereo` |
| `preset_label` | напр. `HEVC remux / Opus · stereo downmix` |
| `ffmpeg_params` | словарь параметров FFmpeg |

Subtitles/data ids в Job API не отдаются (подставляются при resolve).

---

## Ошибки

| Код | Когда |
|-----|--------|
| **400** | Неизвестный `video_id`/`audio_id`; stereo+copy/none; FDK недоступен; нет файлов/путей; batch delete без успехов |
| **207** | Batch: частичный успех (upload / delete) |
| **403** | Internal encode выключен / путь вне roots |
| **404** | Задание / файл не найден |
| **409** | Batch download: часть выходных файлов отсутствует на диске (`missing[]`) |
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
POST /api/jobs/delete  { job_ids }   # опционально, пакетно
```
