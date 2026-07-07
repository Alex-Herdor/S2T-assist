# Pipeline Details

Основной README находится в корне проекта: [`../README.md`](../README.md).

Этот документ описывает внутреннюю структуру локального WhisperX meeting pipeline: зоны хранения, жизненный цикл job, worker-flow, recovery-модель, gold contract, diagnostics, maintenance-инструменты, `jobs.db` и текущую архитектуру CLI.

---

## Главный принцип

Проект строится вокруг локального файлового пайплайна.

Сейчас используются:

* локальная файловая структура;
* SFTP/upload в `data/landing`;
* Windows Task Scheduler worker;
* one-file processing mode;
* восстановимый `data/jobs.db`;
* GitHub Actions smoke CI.

Пока не используются:

* Airflow;
* web UI;
* MinIO/S3;
* облачная обработка;
* полноценный LLM-summary слой.

Ключевой принцип:

```text
сначала устойчивый локальный pipeline
затем jobs.db как индекс
затем API/UI
затем Airflow/другая оркестрация при необходимости
```

---

## Текущий пользовательский слой

Основной пользовательский вход:

```bat
python scripts\pipeline.py <command>
```

Команды:

```bat
python scripts\pipeline.py process
python scripts\pipeline.py status
python scripts\pipeline.py repair --job-id <failed_job_id>
python scripts\pipeline.py doctor
python scripts\pipeline.py init
python scripts\pipeline.py jobs rebuild
python scripts\pipeline.py jobs status
```

`pipeline.py` — это façade над существующими скриптами. Он нужен, чтобы обычная эксплуатация не требовала помнить множество отдельных команд.

---

## Разделение слоёв

```text
Пользовательский / операционный слой:
  scripts/pipeline.py

Автоматический запуск:
  Windows Task Scheduler
  scripts/run_pipeline_worker.bat

Диагностический слой:
  pipeline.py doctor
  scripts/status_jobs.py
  scripts/check_storage_integrity.py
  scripts/recover_orphaned_processing.py

Jobs index слой:
  data/jobs.db
  scripts/rebuild_jobs_db.py
  scripts/jobs_db_status.py
  pipeline.py jobs rebuild/status

Maintenance слой:
  scripts/cleanup_old_jobs.py

Dev / CI слой:
  tests/smoke_tests.py
  .github/workflows/smoke.yml

Локальный safety слой:
  tools/backup_scripts.py

Инженерный слой:
  process_one_file.py
  process_landing_once.py
  retry_failed_job.py
  repair_gold_from_json.py
  run_whisperx.py
  format_whisperx_json.py

Будущий слой:
  API/UI
  Airflow
  LLM-summary
```

---

## Что вызывает `pipeline.py`

| Команда | Назначение | Что используется под капотом |
| --- | --- | --- |
| `process` | Обработка одного `--input` или одного ready-файла из landing | `process_one_file.py` |
| `status` | Read-only статус файлового хранилища | `status_jobs.py` |
| `repair` | Умное восстановление failed job | `repair_gold_from_json.py` / `retry_failed_job.py` |
| `doctor` | Диагностика окружения, storage integrity, jobs.db и orphan summary | встроенные проверки + diagnostic modules |
| `init` | Создание локальных папок | `init_dirs.py` |
| `jobs rebuild` | Пересборка `data/jobs.db` из файловой структуры | `rebuild_jobs_db.py` |
| `jobs status` | Просмотр jobs из `data/jobs.db` | `jobs_db_status.py` |

`process_landing_once.py` остаётся инженерным/legacy-инструментом, но обычный scheduler-flow теперь работает через one-file mode в `pipeline.py process`.

---

## Зоны данных

```text
data/landing
  Входная зона.
  Сюда вручную или через SFTP/upload кладутся аудио/видео файлы.
  Здесь возможны временные файлы и недозагруженные файлы.

data/bronze/raw_original
  Системное хранилище принятых исходников.
  Сюда пишет только pipeline после приёмки файла.

data/processing
  Временная рабочая зона конкретного job_id.
  Здесь создаётся рабочий WAV и job_context.json.

data/silver/audio_flac
  Долгосрочный lossless-аудиоархив.

data/silver/asr_json
  Технический raw WhisperX JSON.

data/gold/transcripts
  Готовый gold-result для дальнейшей обработки.

data/failed
  Diagnostic snapshot для упавших job.

data/archive
  Резерв под будущие сценарии архивации.

data/jobs.db
  Восстановимый SQLite-индекс поверх файловой структуры.

data/.locks
  Lock-файлы для защиты автоматического worker-flow от параллельных запусков.

hf_cache
  Локальный Hugging Face cache.
```

---

## Почему есть `landing → bronze`

`landing` — внешняя входная зона.

Там может быть грязь:

* недогруженный файл;
* временный файл;
* `.uploading`;
* `.part`;
* `.tmp`;
* случайный мусор;
* будущие upload markers.

`bronze/raw_original` — системная зона принятых исходников.

Архитектурный смысл:

```text
внешний мир пишет только в landing
pipeline принимает файл и переносит его в bronze
дальше source of truth — bronze
```

Для MVP это немного избыточно, но важно для будущего UI/SFTP/Airflow.

---

## Файловая готовность

Для SFTP/upload используется паттерн:

```text
file.m4a.uploading
→ file.m4a
```

Pipeline обрабатывает только финальные имена и игнорирует:

```text
.uploading
.tmp
.part
.crdownload
.done
.gitignore
.gitkeep
```

Перед запуском обработки `pipeline.py process` проверяет, что файл достаточно старый и не меняется во время короткого stability probe.

---

## Worker-flow

Автоматическая обработка сделана через Windows Task Scheduler.

Схема:

```text
Task Scheduler
→ scripts/run_pipeline_worker.bat
→ conda activate whisperx-ru
→ python scripts/pipeline.py process
→ максимум один ready-файл
```

Worker-bat отвечает за:

* переход в `C:\whisperx_ru`;
* логирование в `logs/pipeline_worker.log`;
* lock в `data/.locks`;
* активацию conda env;
* запуск `pipeline.py process`;
* возврат exit code.

Рекомендуемые настройки Task Scheduler:

```text
Repeat every: 5 minutes
If the task is already running: Do not start a new instance
Stop the task if it runs longer than: 12 hours
Run task as soon as possible after a scheduled start is missed
```

`pipeline.py process` возвращает `0`, если в `landing` нет готовых файлов. Это штатное состояние для scheduler-flow.

---

## Успешный путь обработки

```text
data/landing/<original_file>
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

После успешной обработки `data/processing/<job_id>` удаляется, если не указан режим `--keep-processing`.

Если cleanup не удался, job всё равно может считаться успешным, но остаётся warning/marker для диагностики.

---

## Gold contract

Gold-result сейчас хранит технический результат, готовый для следующего слоя обработки.

Структура:

```text
data/gold/transcripts/<result_dir>/
├── whisperx_raw.json
├── manifest.json
└── job_context.json
```

### `whisperx_raw.json`

Raw WhisperX JSON.

Обычно содержит:

* `segments`;
* `words`;
* timestamps;
* speaker labels, если была diarization.

Этот файл является основным входом для будущей LLM-постобработки.

### `manifest.json`

Краткий success manifest.

Содержит:

* `job_id`;
* статус;
* дату создания;
* исходное имя файла;
* пути к ключевым результатам.

### `job_context.json`

Полный контекст job.

Содержит:

* `job_id`;
* `status`;
* `current_step`;
* `failed_step`;
* `error_message`;
* `error_traceback`;
* `original_filename`;
* `source_mode`;
* `attempt_type`;
* `retry_of_job_id`;
* timestamps;
* paths.

---

## Почему TXT/MD не основной результат

`format_whisperx_json.py` оставлен как вспомогательный ручной форматтер.

Основной результат сейчас — raw JSON, потому что дальше предполагается LLM-слой:

```text
WhisperX raw JSON
→ нормализация/чанкинг
→ LLM-summary
→ decisions/action items/minutes
```

TXT/MD можно генерировать позже как отдельный presentation layer.

---

## Основные статусы и шаги

Текущий успешный lifecycle:

```text
LANDING_READY
→ BRONZE_ACCEPTED
→ PROCESSING_CREATED
→ WORK_WAV_CREATED
→ ARCHIVE_FLAC_CREATED
→ WHISPERX_RUNNING
→ ASR_JSON_CREATED
→ GOLD_READY
→ SUCCESS
```

В `job_context.json` текущие шаги могут быть такими:

```text
move_to_bronze
use_existing_bronze
create_work_wav
create_archive_flac
run_whisperx
save_silver_asr_json
save_gold_raw_json
gold_ready
```

---

## `source_mode`

`source_mode` показывает, откуда был запущен job.

Примеры:

```text
landing
bronze
silver_asr_json
```

---

## `attempt_type`

`attempt_type` показывает тип попытки.

Примеры:

```text
initial
retry_from_bronze
repair_from_silver
```

---

## Recovery-модель

Есть несколько сценариев восстановления.

### A. Повтор из landing

Если файл ещё не был принят в bronze.

Используется обычный запуск:

```bat
python scripts\pipeline.py process
```

---

### B. Полный retry от bronze

Если WhisperX упал или gold не был создан, но исходник уже есть в bronze:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id> --mode bronze
```

Под капотом:

```bat
python scripts\retry_failed_job.py --job-id <failed_job_id>
```

Создаётся новый job, связанный с исходным через `retry_of_job_id`.

---

### C. Быстрый repair из silver JSON

Если WhisperX уже успешно создал `silver/asr_json/<job_id>.json`, но gold-result не был создан или был повреждён:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id> --mode silver
```

Под капотом:

```bat
python scripts\repair_gold_from_json.py --job-id <failed_job_id>
```

Повторный WhisperX не запускается.

---

### D. Auto repair

Основной пользовательский вариант:

```bat
python scripts\pipeline.py repair --job-id <failed_job_id>
```

Логика:

```text
если есть silver/asr_json:
  repair из silver

иначе если есть bronze/raw_original:
  полный retry от bronze

иначе:
  ошибка — источник восстановления не найден
```

---

## Failed lifecycle

Failed job попадает в:

```text
data/failed/<job_id>
```

Там может быть:

```text
processing/job_context.json
error logs
частичные временные файлы
```

После успешного retry/repair старый failed job не удаляется автоматически.

Вместо этого создаётся marker:

```text
RETRIED_SUCCESSFULLY.json
```

Это позволяет сохранить историю инцидента и при этом понимать, что проблема уже закрыта.

---

## `status_jobs.py`

`status_jobs.py` — read-only аудит файловой структуры.

Через façade:

```bat
python scripts\pipeline.py status
```

Напрямую:

```bat
python scripts\status_jobs.py
```

Поддерживаемые режимы:

```bat
python scripts\pipeline.py status --landing
python scripts\pipeline.py status --processing
python scripts\pipeline.py status --failed
python scripts\pipeline.py status --gold
python scripts\pipeline.py status --json
python scripts\pipeline.py status --sizes
```

`status_jobs.py` не должен изменять файлы.

Служебные файлы `.gitkeep` и `.gitignore` в landing не показываются.

---

## Jobs DB

`data/jobs.db` используется как восстановимый индекс поверх файлового хранилища.

Он не является source of truth.

```text
source of truth:
  files + job_context.json + manifest.json

jobs.db:
  быстрый индекс для status/UI/API
```

Пересборка:

```bat
python scripts\pipeline.py jobs rebuild
python scripts\pipeline.py jobs rebuild --dry-run
```

Просмотр:

```bat
python scripts\pipeline.py jobs status
python scripts\pipeline.py jobs status --details --limit 5
python scripts\pipeline.py jobs status --status FAILED
python scripts\pipeline.py jobs status --search meeting
```

Прямые скрипты:

```bat
python scripts\rebuild_jobs_db.py
python scripts\jobs_db_status.py
```

Минимальная таблица `jobs`:

```text
job_id
original_filename
status
current_step
source_mode
attempt_type
retry_of_job_id
bronze_path
silver_json_path
gold_result_dir
failed_dir
processing_dir
created_at
updated_at
error_message
discovered_from
```

Приоритет статусов при merge:

```text
SUCCESS
RETRIED_SUCCESSFULLY
FAILED
PROCESSING
ASR_JSON_ONLY
BRONZE_ONLY
```

Если `jobs.db` повреждён или удалён, его можно пересобрать из файлов.

---

## `doctor`

`doctor` проверяет готовность локальной установки:

```bat
python scripts\pipeline.py doctor
```

Проверяет:

* наличие основных папок;
* доступ на запись;
* наличие `config/whisperx_config.json`;
* наличие `.env`, если он нужен;
* корректность JSON-конфига;
* `hf_cache_dir`;
* наличие HF token, если включена diarization;
* доступность `ffmpeg`;
* доступность `whisperx`;
* `py_compile` основных Python-скриптов;
* краткую storage integrity summary;
* состояние `jobs.db`;
* краткую orphaned processing summary.

Подробный режим:

```bat
python scripts\pipeline.py doctor --verbose
```

JSON-режим:

```bat
python scripts\pipeline.py doctor --json
```

Отсутствие `jobs.db` считается warning, а не error.

---

## Storage integrity

Детальная read-only проверка файлового хранилища:

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

Порог orphan processing:

```bat
python scripts\check_storage_integrity.py --orphan-hours 6
```

Strict mode:

```bat
python scripts\check_storage_integrity.py --strict
```

Скрипт проверяет:

```text
gold есть, но нет whisperx_raw.json
gold есть, но нет manifest.json
gold есть, но нет job_context.json
failed есть, но нет job_context.json
failed retry marker невалиден
silver/asr_json есть, но нет gold
processing висит слишком долго
CLEANUP_FAILED.txt есть
bronze есть, но нет связанного job_id
```

Служебные `.gitkeep` / `.gitignore` игнорируются.

Скрипт ничего не исправляет и ничего не удаляет.

---

## Orphaned processing recovery check

Read-only анализ зависших `data/processing/<job_id>`:

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

Показать свежие job:

```bat
python scripts\recover_orphaned_processing.py --include-recent --verbose
```

Проверить конкретный job:

```bat
python scripts\recover_orphaned_processing.py --job-id <job_id> --verbose
```

Возможные классификации:

```text
recent_processing
orphan_missing_context
orphan_invalid_context
orphan_gold_exists
orphan_silver_without_gold
orphan_failed_exists
orphan_bronze_available
orphan_no_recovery_source
```

Скрипт ничего не удаляет, не перемещает и не чинит.

---

## Cleanup old jobs

`cleanup_old_jobs.py` — maintenance-инструмент для анализа и безопасной очистки старых технических артефактов.

Диагностический режим:

```bat
python scripts\cleanup_old_jobs.py --verbose
```

JSON:

```bat
python scripts\cleanup_old_jobs.py --json
```

Фильтр по уровню:

```bat
python scripts\cleanup_old_jobs.py --level safe --verbose
python scripts\cleanup_old_jobs.py --level caution --verbose
```

Фильтр по категории:

```bat
python scripts\cleanup_old_jobs.py --category local_backups --include-young-backups --verbose
```

### Уровни

```text
safe
  можно удалять через явный delete-флаг и --yes

caution
  только подсветка, без удаления

info
  информационные кандидаты
```

### SAFE-категории

```text
pycache
backup_files
local_backups
logs
```

### CAUTION-категории

```text
retried_failed
old_processing
processing_cleanup_failed
```

CAUTION-категории только подсвечиваются. Удаляющего режима для них сейчас нет.

### Safe delete

План удаления без фактического удаления:

```bat
python scripts\cleanup_old_jobs.py --delete-pycache
```

Реальное удаление:

```bat
python scripts\cleanup_old_jobs.py --delete-pycache --yes
```

Другие safe-delete команды:

```bat
python scripts\cleanup_old_jobs.py --delete-backup-files-older-than-days 0 --yes
python scripts\cleanup_old_jobs.py --delete-local-backups-older-than-days 14 --yes
python scripts\cleanup_old_jobs.py --delete-logs-older-than-days 14 --yes
```

Без `--yes` удаление не выполняется, а выводятся `PLANNED` результаты.

Скрипт не удаляет:

```text
bronze
silver
gold
retried_failed
old_processing
processing_cleanup_failed
```

---

## Smoke tests

Smoke tests — это dev/CI-инструмент, а не часть пользовательского pipeline flow.

Запуск:

```bat
python tests\smoke_tests.py
```

В CI или окружении без WhisperX/ffmpeg:

```bat
python tests\smoke_tests.py --skip-doctor
```

Если локальные данные временно находятся в переходном состоянии:

```bat
python tests\smoke_tests.py --skip-integrity
```

Проверяет:

* синтаксис основных скриптов;
* help-команды `pipeline.py`;
* help-команды `pipeline.py jobs`;
* `pipeline.py init`;
* `pipeline.py status --json`;
* `pipeline.py process --dry-run --show-sizes`;
* `check_storage_integrity.py`;
* опционально `pipeline.py doctor`.

Smoke tests не запускают реальную транскрибацию.

---

## GitHub Actions

Минимальный CI workflow находится здесь:

```text
.github/workflows/smoke.yml
```

Он запускает:

```bash
python tests/smoke_tests.py --skip-doctor --skip-integrity
```

Это не end-to-end тест ASR pipeline. GitHub Actions используется только как быстрый предохранитель от поломки кода и CLI-контрактов.

Что проверяется в CI:

```text
py_compile основных скриптов
pipeline.py --help
pipeline.py process/status/repair/doctor/init/jobs --help
pipeline.py jobs rebuild/status --help
pipeline.py init
pipeline.py status --json
pipeline.py process --dry-run --show-sizes
```

Что намеренно не проверяется в CI:

```text
WhisperX
ffmpeg
Hugging Face token
локальный whisperx_config.json
реальное data-хранилище
storage integrity реальных job
jobs.db runtime state
реальная транскрибация
```

Причина: CI не является копией локального mini-PC. Его задача — быстро показать, что изменения в репозитории не сломали базовый Python/CLI слой.

Локальная проверка остаётся более полной:

```bat
python tests\smoke_tests.py --skip-doctor
python tests\smoke_tests.py
```

---

## Локальный backup перед правками

Для быстрой страховки перед ручными изменениями используется:

```bat
python tools\backup_scripts.py --include-docs --label before_next_edit
```

По умолчанию сохраняются:

```text
scripts/*.py
tests/*.py
```

С `--include-docs` дополнительно сохраняются:

```text
README.md
docs/*.md
config/*.example.json
.env.example
.gitignore
environment.yml
```

Backup складывается в:

```text
.local_backups/scripts_<YYYYMMDD_HHMMSS>[_label]
```

`.local_backups` не попадает в Git.

---

## Cleanup behavior после обработки

После успешной обработки processing-папка удаляется.

Если удалить не удалось:

* job не считается failed;
* создаётся warning;
* может остаться `CLEANUP_FAILED.txt`.

Это не должно ломать результат обработки.

---

## Будущий LLM-слой

Следующий продуктовый слой после ASR:

```text
gold/transcripts/<result_dir>/whisperx_raw.json
→ prepare_llm_input
→ LLM-summary
→ decisions/action_items/minutes
```

Пока LLM-слой не реализован.

Важно не смешивать ASR pipeline и LLM-summary в один монолитный скрипт.

---

## Будущий UI

UI должен появиться после стабилизации файлового слоя и `jobs.db`.

Минимальные функции UI:

```text
загрузка файла
список jobs
статус job
детали job
retry/repair
скачивание результата
запуск диагностики
maintenance summary
```

UI не должен напрямую реализовывать бизнес-логику обработки. Он должен вызывать уже проверенный backend/CLI/API слой.

---

## Будущий Airflow

Airflow нужен позже как оркестратор, а не как хранилище.

Первый DAG должен быть тонким:

```text
scan landing
→ call pipeline.py process
→ report status
```

Airflow не должен хранить большие файлы в XCom.

Передавать можно только:

```text
job_id
пути
статусы
короткие ошибки
```

---

## Что не усложнять сейчас

Пока не добавлять без отдельного решения:

* Airflow;
* web UI;
* MinIO/S3;
* VPS;
* Cloudflare Tunnel;
* VTT;
* веб-плеер;
* сложный speaker remapping;
* оптимизацию diarization;
* cloud processing.

---

## Принцип дальнейшего развития

```text
Обычная эксплуатация не должна требовать много команд.
Новые инструменты могут появляться, но не как обязательные ручные шаги.
Инженерные скрипты должны быть доступны напрямую.
Пользовательский слой должен оставаться простым.
Будущий UI/API должен использовать те же контракты, что CLI.
Сначала устойчивость и наблюдаемость, потом UI/API/Airflow.
```