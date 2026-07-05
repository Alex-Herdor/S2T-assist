# Pipeline Details

Подробное описание устройства локального пайплайна обработки встреч через WhisperX.

Основной README находится в корне проекта: [`../README.md`](../README.md).

---

## Общая идея

Проект строит файловую обвязку вокруг WhisperX.

Основной путь данных:

```text
landing
→ bronze
→ processing
→ silver
→ gold
```

Путь восстановления после ошибок:

```text
failed
→ retry from bronze
→ repair from silver
→ status audit
```

---

## Зоны хранения

| Зона                       | Назначение                                                                  |
| -------------------------- | --------------------------------------------------------------------------- |
| `data/landing`             | Входная зона. Сюда вручную кладутся аудио/видео файлы.                      |
| `data/bronze/raw_original` | Системное хранилище принятых исходников. Источник истины для полного retry. |
| `data/processing/<job_id>` | Временная рабочая папка конкретной обработки.                               |
| `data/silver/audio_flac`   | Долгосрочный lossless-аудиоархив.                                           |
| `data/silver/asr_json`     | Технический raw WhisperX JSON. Источник для быстрого repair.                |
| `data/gold/transcripts`    | Готовые результаты для пользователя или следующей LLM-обработки.            |
| `data/failed`              | Диагностические слепки упавших jobs.                                        |
| `data/archive`             | Зарезервировано под будущие сценарии архивирования.                         |

---

## Почему есть `landing` и `bronze`

`landing → bronze` — это архитектурная граница.

`landing` считается внешней и потенциально грязной зоной. Там могут быть:

* недокопированные файлы;
* временные `.uploading`;
* `.part`;
* `.tmp`;
* ошибки загрузки;
* будущие `.done` markers.

`bronze/raw_original` — системная зона. Туда пишет только пайплайн после приёмки файла.

Для ручного MVP это немного избыточно, но полезно для будущего развития: SFTP, веб-загрузка, Airflow или watcher смогут писать во входную зону, не трогая системное raw-хранилище.

---

## Success path

Успешная обработка:

```text
data/landing
→ data/bronze/raw_original/<job_id>.<ext>
→ data/processing/<job_id>/<original_stem>__work.wav
→ data/silver/audio_flac/<job_id>.flac
→ WhisperX
→ data/silver/asr_json/<job_id>.json
→ data/gold/transcripts/<original_stem>__<YYYYMMDD_HHMMSS>__<hash>/
   ├── whisperx_raw.json
   ├── manifest.json
   └── job_context.json
```

---

## Gold contract

Итоговая gold-папка:

```text
data/gold/transcripts/<result_dir>/
├── whisperx_raw.json
├── manifest.json
└── job_context.json
```

### `whisperx_raw.json`

Копия raw WhisperX JSON.

Сохраняет `segments` и `words` / word-level тайминги. Это основной вход для следующей LLM-обработки.

### `manifest.json`

Краткий manifest успешного результата:

* `job_id`;
* `status`;
* `source_mode`;
* `attempt_type`;
* `retry_of_job_id`;
* пути к основным артефактам;
* информация о raw JSON.

### `job_context.json`

Полный контекст job:

* исходное имя файла;
* текущий статус;
* текущий/последний шаг;
* тип запуска;
* пути к артефактам;
* error message / traceback при падении;
* timestamps.

---

## Source mode и attempt type

В `job_context.json` и `manifest.json` используются поля:

| Поле              | Пример               | Значение                            |
| ----------------- | -------------------- | ----------------------------------- |
| `source_mode`     | `landing`            | Обычная обработка из landing.       |
| `source_mode`     | `bronze`             | Retry от уже принятого исходника.   |
| `source_mode`     | `silver_asr_json`    | Repair из готового WhisperX JSON.   |
| `attempt_type`    | `initial`            | Первичная обработка.                |
| `attempt_type`    | `retry`              | Повторная обработка от bronze.      |
| `attempt_type`    | `repair_from_silver` | Восстановление gold из silver JSON. |
| `retry_of_job_id` | `<old_job_id>`       | Ссылка на старый failed job.        |

---

## Failed lifecycle

Failed job может иметь один из lifecycle-статусов.

| Статус                          | Значение                                                                                   |
| ------------------------------- | ------------------------------------------------------------------------------------------ |
| `failed_active`                 | Job упала и ещё не была успешно восстановлена.                                             |
| `retried_successfully_inferred` | Успешный retry/repair найден по `gold/job_context.json`, но marker в failed ещё не создан. |
| `retried_successfully_marked`   | В failed-папке есть `RETRIED_SUCCESSFULLY.json`.                                           |

Marker находится здесь:

```text
data/failed/<old_job_id>/RETRIED_SUCCESSFULLY.json
```

Пример marker:

```json
{
  "status": "retried_successfully",
  "old_job_id": "20260705_120000_meeting_abcd1234",
  "retry_source": "bronze",
  "marked_at": "2026-07-05T15:30:00",
  "latest_retry": {
    "new_job_id": "20260705_153000_meeting_efgh5678",
    "attempt_type": "retry",
    "source_mode": "bronze",
    "retry_of_job_id": "20260705_120000_meeting_abcd1234",
    "gold_result_dir": "<PROJECT_ROOT>\\data\\gold\\transcripts\\meeting__20260705_153000__efgh5678"
  },
  "all_successful_retries": [],
  "notes": "Старый failed не удалён автоматически. Он сохранён как диагностический слепок исходной ошибки."
}
```

---

## Retry от bronze

Полный retry используется, если WhisperX не дошёл до создания JSON.

Команда:

```bat
python scripts\retry_failed_job.py --job-id <failed_job_id>
```

Логика:

```text
failed/<old_job_id>/processing/job_context.json
→ bronze/raw_original/<old_job_id>.<ext>
→ process_one_file.py --from-bronze
→ новый successful gold
→ failed/<old_job_id>/RETRIED_SUCCESSFULLY.json
```

`retry_source`:

```json
"bronze"
```

Старый failed не удаляется автоматически. Он остаётся диагностическим слепком исходной ошибки.

---

## Repair из silver JSON

Быстрый repair используется, если WhisperX уже создал JSON, но `gold` отсутствует или некорректен.

Команда:

```bat
python scripts\repair_gold_from_json.py --job-id <failed_job_id>
```

Логика:

```text
silver/asr_json/<job_id>.json
→ gold/transcripts/<result_dir>/whisperx_raw.json
→ gold/transcripts/<result_dir>/manifest.json
→ gold/transcripts/<result_dir>/job_context.json
→ failed/<old_job_id>/RETRIED_SUCCESSFULLY.json
```

`retry_source`:

```json
"silver_asr_json"
```

Это экономный сценарий: WhisperX не запускается повторно.

---

## Как выбрать способ восстановления

| Ситуация                                                        | Что делать                                                                 |
| --------------------------------------------------------------- | -------------------------------------------------------------------------- |
| WhisperX упал до создания JSON                                  | `retry_failed_job.py --job-id <failed_job_id>`                             |
| `silver/asr_json/<job_id>.json` уже есть, но `gold` отсутствует | `repair_gold_from_json.py --job-id <failed_job_id>`                        |
| `gold` есть, но старый failed не отмечен                        | проверить `status_jobs.py --failed-only`                                   |
| Осталась папка `processing` после success                       | проверить `CLEANUP_FAILED.txt`; это cleanup warning, не обязательно failed |
| В `landing` несколько файлов                                    | `process_landing_once.py`                                                  |
| Нужно понять состояние системы                                  | `status_jobs.py`                                                           |

---

## Что происходит при ошибке

Если `process_one_file.py` падает, он переносит рабочую папку в:

```text
data/failed/<job_id>/processing
```

Обычно там есть:

```text
data/failed/<job_id>/
├── ERROR.txt
└── processing
    ├── job_context.json
    ├── logs
    │   └── process.log
    └── ...
```

`failed/processing` — диагностический слепок, а не источник истины для retry.

Источник истины для полного retry:

```text
data/bronze/raw_original
```

Источник истины для быстрого repair:

```text
data/silver/asr_json
```

---

## Cleanup behavior

После успешной обработки `processing/<job_id>` обычно удаляется.

Если удалить не удалось, job не считается failed, потому что результат уже создан в `gold`.

В этом случае остаётся:

```text
data/processing/<job_id>/CLEANUP_FAILED.txt
```

`status_jobs.py` покажет это как cleanup warning.

---

## Проверка состояния

Основная команда:

```bat
python scripts\status_jobs.py
```

Полезные варианты:

```bat
python scripts\status_jobs.py --failed-only
python scripts\status_jobs.py --processing-only
python scripts\status_jobs.py --gold-only
python scripts\status_jobs.py --landing-only
python scripts\status_jobs.py --sizes
python scripts\status_jobs.py --json
```

---

## Файловая готовность

В текущем ручном MVP `.done` marker не требуется.

`process_landing_once.py` обрабатывает готовые файлы из `data/landing` и игнорирует временные расширения:

```text
.done
.uploading
.part
.tmp
.crdownload
```

В будущем для загрузки можно использовать схему:

```text
file.uploading
→ rename to file.m4a
→ optional file.done
```

---

## Будущее развитие

Возможные следующие шаги:

* `cleanup_old_jobs.py` с `--dry-run`;
* `recover_orphaned_processing.py`;
* `jobs.db` для продуктового статуса;
* Airflow DAG поверх `process_one_file.py`;
* веб-интерфейс загрузки/скачивания;
* SFTP/upload;
* speaker remapping;
* генерация user-friendly TXT/MD отдельным этапом;
* VTT/WebVTT;
* веб-плеер.

Airflow должен быть оркестратором, а не хранилищем больших файлов. В XCom следует передавать только `job_id`, пути и статусы.
