# Local WhisperX Meeting Pipeline

Локальный пайплайн для обработки аудио/видео записей рабочих встреч через WhisperX на Windows 11.

Цель проекта — принимать записи встреч, локально обрабатывать их через WhisperX, сохранять технический ASR JSON и готовить основу для дальнейшей постобработки, UI/API и LLM-summary слоя.

Проект рассчитан на локальный запуск на mini-PC без облачной обработки.

Подробности по устройству пайплайна: [`docs/PIPELINE_DETAILS.md`](docs/PIPELINE_DETAILS.md).

---

## Текущее состояние MVP

Сейчас реализован локальный semi-automated pipeline:

1. файл загружается в `data/landing` вручную или через SFTP;
2. для SFTP/upload используется безопасный паттерн `*.uploading → final filename`;
3. Windows Task Scheduler периодически запускает `scripts/run_pipeline_worker.bat`;
4. worker активирует conda-окружение `whisperx-ru`;
5. worker вызывает `python scripts/pipeline.py process`;
6. `pipeline.py process` выбирает максимум один готовый файл из `data/landing`;
7. временные и недогруженные файлы игнорируются;
8. перед обработкой проверяется стабильность файла;
9. исходник переносится в `data/bronze/raw_original`;
10. создаётся рабочий WAV в `data/processing`;
11. создаётся FLAC-архив в `data/silver/audio_flac`;
12. запускается WhisperX;
13. raw WhisperX JSON сохраняется в `data/silver/asr_json`;
14. итоговый gold-result сохраняется в `data/gold/transcripts`;
15. временная processing-папка удаляется после успешной обработки;
16. при ошибке создаётся diagnostic snapshot в `data/failed`;
17. `data/jobs.db` можно пересобрать из файлов и использовать для быстрого просмотра jobs.

---

## Что входит в MVP

* локальная обработка аудио/видео файлов;
* SFTP/upload в `data/landing`;
* безопасная файловая готовность через `.uploading → final filename`;
* автоматический запуск через Windows Task Scheduler;
* worker-bat для запуска pipeline;
* one-file processing mode для безопасной работы по расписанию;
* единая пользовательская точка входа `scripts/pipeline.py`;
* обработка одного конкретного файла через `--input`;
* сохранение исходника в `bronze`;
* создание рабочего WAV;
* создание архивного FLAC;
* запуск WhisperX;
* сохранение raw WhisperX JSON;
* сохранение gold-result с `whisperx_raw.json`, `manifest.json`, `job_context.json`;
* retry failed job от bronze;
* repair gold из уже готового silver JSON;
* диагностика окружения через `pipeline.py doctor`;
* read-only статус локального хранилища;
* проверка целостности файлового хранилища;
* анализ зависших `processing` jobs;
* безопасная диагностика и очистка локальных технических артефактов;
* восстановимый SQLite-индекс `data/jobs.db`;
* просмотр jobs через `pipeline.py jobs status`;
* smoke tests для разработки;
* GitHub Actions smoke CI;
* локальный backup скриптов перед ручными правками.

---

## Что пока не входит в MVP

* Airflow;
* веб-интерфейс;
* MinIO/S3;
* Cloudflare Tunnel / VPS relay;
* VTT;
* веб-плеер;
* сложный speaker remapping;
* оптимизация diarization;
* облачная обработка;
* полноценный LLM-summary слой.

Эти части могут быть добавлены позже, когда локальный файловый слой, jobs-index и CLI/API-контракты будут достаточно устойчивыми.

---

## Требования

* Windows 11;
* Python / conda;
* установленный `ffmpeg`;
* установленный и рабочий WhisperX;
* локальное conda-окружение;
* Hugging Face token, если включена diarization;
* локальный Hugging Face cache.

Базовый локальный путь проекта в рабочей установке:

```bat
C:\whisperx_ru
```

Базовое conda-окружение:

```bat
whisperx-ru
```

---

## Быстрый старт

Перейти в папку проекта:

```bat
cd /d C:\whisperx_ru
conda activate whisperx-ru
```

Создать локальные папки после clone или переноса проекта:

```bat
python scripts\pipeline.py init
```

Проверить окружение:

```bat
python scripts\pipeline.py doctor
```

Положить файл встречи в:

```text
data\landing
```

Запустить обработку одного готового файла из landing:

```bat
python scripts\pipeline.py process
```

Проверить состояние файлового хранилища:

```bat
python scripts\pipeline.py status
```

Пересобрать jobs index:

```bat
python scripts\pipeline.py jobs rebuild
```

Посмотреть jobs:

```bat
python scripts\pipeline.py jobs status
```

---

## Основной CLI

Основной пользовательский CLI:

```bat
python scripts\pipeline.py <command>
```

Доступные команды:

```bat
python scripts\pipeline.py process
python scripts\pipeline.py status
python scripts\pipeline.py repair --job-id <failed_job_id>
python scripts\pipeline.py doctor
python scripts\pipeline.py init
python scripts\pipeline.py jobs rebuild
python scripts\pipeline.py jobs status
```

`pipeline.py` — это тонкий façade над существующими скриптами. Он не заменяет инженерные инструменты, а даёт единый вход для обычной эксплуатации, worker-а и будущего UI/API.

---

## Обработка файлов

### Автоматическая обработка landing

Обычный режим:

```bat
python scripts\pipeline.py process
```

Команда выбирает максимум один готовый файл из:

```text
data\landing
```

Если файлов нет, это штатная ситуация:

```text
[process] no files found in landing
exit code: 0
```

Один запуск `process` обрабатывает максимум один файл. Следующий файл будет подхвачен следующим запуском worker-а или ручным повторным запуском команды.

---

### Обработка конкретного файла

```bat
python scripts\pipeline.py process --input data\landing\meeting.m4a
```

---

### Файловая готовность

Для загрузки через SFTP/upload используется паттерн:

```text
meeting.m4a.uploading
→ meeting.m4a
```

Pipeline игнорирует:

```text
.uploading
.tmp
.part
.crdownload
.done
.gitignore
.gitkeep
```

Перед запуском обработки проверяется, что файл достаточно старый и не меняется во время короткой stability probe.

---

## Windows Task Scheduler Worker

Автоматический запуск сделан через Windows Task Scheduler.

В Git хранится только безопасный шаблон:

```bat
scripts\run_pipeline_worker.bat.example
```

Локальный рабочий файл создаётся из шаблона и не коммитится:

```bat
scripts\run_pipeline_worker.bat
```

`run_pipeline_worker.bat` не попадает в Git, потому что содержит локальные пути к проекту и conda.

Worker:

* переходит в локальную папку проекта;
* пишет лог в `logs\pipeline_worker.log`;
* ставит lock в `data\.locks`;
* активирует conda-окружение `whisperx-ru`;
* запускает `python scripts\pipeline.py process`;
* после каждого запуска пересобирает `data\jobs.db`;
* возвращает понятный exit code.

Логика exit code:

```text
process упал
  → worker возвращает код process

process успешен, jobs rebuild упал
  → worker возвращает код jobs rebuild

process успешен, jobs rebuild успешен
  → worker возвращает 0
```

Рекомендуемые настройки задачи:

```text
Repeat every: 5 minutes
If the task is already running: Do not start a new instance
Stop the task if it runs longer than: 12 hours
Run task as soon as possible after a scheduled start is missed
```

На время ручных правок скриптов задачу лучше отключать:

```bat
schtasks /Change /TN "WhisperX Pipeline Worker" /Disable
```

После проверки включить обратно:

```bat
schtasks /Change /TN "WhisperX Pipeline Worker" /Enable
```

---

## Проверка статуса

Файловый статус:

```bat
python scripts\pipeline.py status
```

Только landing:

```bat
python scripts\pipeline.py status --landing
```

Только failed:

```bat
python scripts\pipeline.py status --failed
```

Только gold:

```bat
python scripts\pipeline.py status --gold
```

JSON-вывод:

```bat
python scripts\pipeline.py status --json
```

---

## Jobs DB

`data/jobs.db` — восстановимый SQLite-индекс поверх файловой структуры.

Source of truth остаётся файловым:

```text
data/bronze
data/processing
data/silver
data/gold
data/failed
job_context.json
manifest.json
```

`jobs.db` нужен для быстрого просмотра jobs и будущего UI/API. Если база повреждена или удалена, её можно пересобрать из файлов.

Пересобрать индекс:

```bat
python scripts\pipeline.py jobs rebuild
```

Dry-run:

```bat
python scripts\pipeline.py jobs rebuild --dry-run
```

Посмотреть jobs:

```bat
python scripts\pipeline.py jobs status
python scripts\pipeline.py jobs status --details --limit 5
python scripts\pipeline.py jobs status --status FAILED
python scripts\pipeline.py jobs status --search meeting
```

Прямые скрипты остаются доступны:

```bat
python scripts\rebuild_jobs_db.py
python scripts\jobs_db_status.py
```

`doctor` проверяет наличие и читаемость `jobs.db`. Отсутствие базы считается warning, а не error.

---

## Диагностика

```bat
python scripts\pipeline.py doctor
```

`doctor` проверяет:

* структуру папок;
* доступ на запись;
* локальный конфиг;
* `.env`;
* `ffmpeg`;
* `whisperx`;
* HF token при включённой diarization;
* синтаксис основных Python-скриптов;
* краткую storage integrity summary;
* состояние `jobs.db`;
* краткую orphaned processing summary.

Подробный вывод:

```bat
python scripts\pipeline.py doctor --verbose
```

JSON-вывод:

```bat
python scripts\pipeline.py doctor --json
```

---

## Восстановление failed job

Автоматически выбрать лучший способ восстановления:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id>
```

Dry-run:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id> --dry-run
```

Принудительно repair из silver JSON:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id> --mode silver
```

Принудительно полный retry от bronze:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id> --mode bronze
```

В режиме `auto` логика такая:

1. если есть `silver/asr_json/<job_id>.json`, выполняется быстрый repair gold;
2. если silver JSON нет, но есть bronze original, выполняется полный retry от bronze;
3. если нет ни silver, ни bronze, выводится ошибка.

---

## Инженерные скрипты

Низкоуровневые скрипты остаются доступны напрямую:

```text
scripts\process_one_file.py
scripts\process_landing_once.py
scripts\status_jobs.py
scripts\retry_failed_job.py
scripts\repair_gold_from_json.py
scripts\run_whisperx.py
scripts\init_dirs.py
scripts\rebuild_jobs_db.py
scripts\jobs_db_status.py
```

Они нужны для отладки, диагностики и точечного восстановления.

Обычный пользовательский flow должен идти через:

```bat
python scripts\pipeline.py ...
```

---

## Диагностические и maintenance-инструменты

### Проверка целостности хранилища

```bat
python scripts\check_storage_integrity.py
```

Подробно:

```bat
python scripts\check_storage_integrity.py --verbose
```

JSON:

```bat
python scripts\check_storage_integrity.py --json
```

Strict mode:

```bat
python scripts\check_storage_integrity.py --strict
```

Скрипт ничего не удаляет и не исправляет.

---

### Анализ зависших processing job

```bat
python scripts\recover_orphaned_processing.py
```

Подробно:

```bat
python scripts\recover_orphaned_processing.py --verbose
```

JSON:

```bat
python scripts\recover_orphaned_processing.py --json
```

Проверить конкретный job:

```bat
python scripts\recover_orphaned_processing.py --job-id <job_id> --verbose
```

Скрипт ничего не удаляет, не перемещает и не чинит.

---

### Диагностика и безопасная очистка старых технических артефактов

Диагностический режим:

```bat
python scripts\cleanup_old_jobs.py --verbose
```

JSON:

```bat
python scripts\cleanup_old_jobs.py --json
```

Показать safe-кандидатов:

```bat
python scripts\cleanup_old_jobs.py --level safe --verbose
```

Показать caution-кандидатов:

```bat
python scripts\cleanup_old_jobs.py --level caution --verbose
```

SAFE-категории, которые можно удалять через явные флаги:

```text
pycache
backup_files
local_backups
logs
```

Реальное удаление требует `--yes`:

```bat
python scripts\cleanup_old_jobs.py --delete-pycache --yes
```

`bronze`, `silver` и `gold` этим инструментом не удаляются.

---

## Dev-инструменты

### Smoke tests

```bat
python tests\smoke_tests.py
```

Если нужно пропустить `doctor`:

```bat
python tests\smoke_tests.py --skip-doctor
```

Если нужно временно пропустить storage integrity:

```bat
python tests\smoke_tests.py --skip-integrity
```

Smoke tests не запускают реальную транскрибацию. Они проверяют:

* `py_compile` основных скриптов;
* help-команды `pipeline.py`;
* help-команды `pipeline.py jobs`;
* `pipeline.py init`;
* `pipeline.py status --json`;
* `pipeline.py process --dry-run --show-sizes`;
* `check_storage_integrity.py`;
* опционально `pipeline.py doctor`.

---

### GitHub Actions

Минимальный CI workflow:

```text
.github/workflows/smoke.yml
```

CI-команда:

```bash
python tests/smoke_tests.py --skip-doctor --skip-integrity
```

В GitHub Actions не запускаются:

```text
pipeline.py doctor
storage integrity check
реальная WhisperX-обработка
```

Причина: в CI нет локального Windows/conda окружения, WhisperX-моделей, ffmpeg, `.env`, Hugging Face cache и реальных файлов `data`.

---

### Локальный backup скриптов

Перед ручными правками можно создать локальный backup:

```bat
python tools\backup_scripts.py --include-docs --label before_next_edit
```

Backup складывается в:

```text
.local_backups
```

Эта папка не попадает в Git.

---

## Структура проекта

```text
C:\whisperx_ru
├── data
│   ├── landing
│   ├── bronze
│   │   └── raw_original
│   ├── processing
│   ├── silver
│   │   ├── audio_flac
│   │   └── asr_json
│   ├── gold
│   │   └── transcripts
│   ├── failed
│   ├── archive
│   └── jobs.db
├── logs
│   └── pipeline_worker.log
├── scripts
│   ├── pipeline.py
│   ├── run_pipeline_worker.bat.example
│   ├── run_whisperx.py
│   ├── process_one_file.py
│   ├── process_landing_once.py
│   ├── status_jobs.py
│   ├── retry_failed_job.py
│   ├── repair_gold_from_json.py
│   ├── check_storage_integrity.py
│   ├── recover_orphaned_processing.py
│   ├── cleanup_old_jobs.py
│   ├── rebuild_jobs_db.py
│   ├── jobs_db_status.py
│   ├── format_whisperx_json.py
│   ├── project_paths.py
│   └── init_dirs.py
├── tests
│   └── smoke_tests.py
├── tools
│   └── backup_scripts.py
├── docs
│   └── PIPELINE_DETAILS.md
├── config
│   └── whisperx_config.json
└── hf_cache
```

---

## Важно про Git

В Git не должны попадать:

```text
.env
data/jobs.db
data/jobs.db-*
logs/
.local_backups/
hf_cache/
локальные audio/video данные
```