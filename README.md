# Local WhisperX Meeting Pipeline

Локальный пайплайн для обработки аудио/видео записей рабочих встреч через WhisperX на Windows 11.

Цель проекта — принимать записи встреч, локально обрабатывать их через WhisperX, сохранять технический JSON и готовить данные для дальнейшей постобработки, UI и автоматизации.

Проект рассчитан на локальный запуск на mini-PC без облачной обработки.

Подробности по устройству пайплайна: [`docs/PIPELINE_DETAILS.md`](docs/PIPELINE_DETAILS.md).

---

## Текущее состояние MVP

Сейчас реализован локальный файловый пайплайн:

1. файл вручную кладётся в `data/landing`;
2. запускается единая CLI-команда;
3. файл переносится в `data/bronze/raw_original`;
4. создаётся рабочий WAV в `data/processing`;
5. создаётся FLAC-архив в `data/silver/audio_flac`;
6. запускается WhisperX;
7. raw WhisperX JSON сохраняется в `data/silver/asr_json`;
8. итоговый gold-result сохраняется в `data/gold/transcripts`;
9. временная processing-папка удаляется после успешной обработки;
10. при ошибке создаётся diagnostic snapshot в `data/failed`.

---

## Что входит в MVP

* локальная обработка аудио/видео файлов;
* единая пользовательская точка входа `scripts/pipeline.py`;
* ручная batch-обработка файлов из `data/landing`;
* обработка одного конкретного файла;
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
* smoke tests для разработки;
* локальный backup скриптов перед ручными правками.

---

## Что пока не входит в MVP

* Airflow;
* веб-интерфейс;
* сетевой upload;
* SFTP-интеграция;
* MinIO/S3;
* Cloudflare Tunnel / VPS relay;
* VTT;
* веб-плеер;
* сложный speaker remapping;
* оптимизация diarization;
* облачная обработка;
* jobs.db;
* LLM-summary слой.

Эти части могут быть добавлены позже, когда локальный файловый пайплайн станет достаточно устойчивым.

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

Проверить, какие файлы будут обработаны:

```bat
python scripts\pipeline.py process --dry-run --show-sizes
```

Запустить обработку landing:

```bat
python scripts\pipeline.py process
```

Проверить состояние хранилища:

```bat
python scripts\pipeline.py status
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
```

`pipeline.py` — это тонкий façade над существующими скриптами. Он не заменяет инженерные инструменты, а даёт единый вход для обычной эксплуатации и будущего UI/API.

---

## Основные команды

### Обработка всех готовых файлов из landing

```bat
python scripts\pipeline.py process
```

Dry-run:

```bat
python scripts\pipeline.py process --dry-run --show-sizes
```

Ограничить количество файлов:

```bat
python scripts\pipeline.py process --limit 2
```

Продолжать после ошибки:

```bat
python scripts\pipeline.py process --continue-on-error
```

---

### Обработка одного файла

```bat
python scripts\pipeline.py process --input data\landing\meeting.m4a
```

---

### Проверка статуса

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

JSON-вывод:

```bat
python scripts\pipeline.py status --json
```

---

### Диагностика

```bat
python scripts\pipeline.py doctor
```

Подробный вывод:

```bat
python scripts\pipeline.py doctor --verbose
```

JSON-вывод:

```bat
python scripts\pipeline.py doctor --json
```

---

### Восстановление failed job

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
```

Они нужны для отладки, диагностики и точечного восстановления.

Обычный пользовательский flow должен идти через:

```bat
python scripts\pipeline.py ...
```

---

## Dev-инструменты

### Smoke tests

Smoke tests находятся отдельно от эксплуатационного CLI:

```bat
python tests\smoke_tests.py
```

Если нужно пропустить `doctor`:

```bat
python tests\smoke_tests.py --skip-doctor
```

JSON-вывод:

```bat
python tests\smoke_tests.py --json
```

Smoke tests не запускают реальную транскрибацию. Они проверяют:

* `py_compile` основных скриптов;
* help-команды `pipeline.py`;
* `pipeline.py init`;
* `pipeline.py status --json`;
* `pipeline.py process --dry-run --show-sizes`;
* опционально `pipeline.py doctor`.

---

### Локальный backup скриптов

Перед ручными правками можно создать локальный backup:

```bat
python tools\backup_scripts.py --include-docs --label before_next_edit
```

Только список файлов:

```bat
python tools\backup_scripts.py --list
```

Dry-run:

```bat
python tools\backup_scripts.py --dry-run
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
│   └── archive
├── scripts
│   ├── pipeline.py
│   ├── run_whisperx.py
│   ├── process_one_file.py
│   ├── process_landing_once.py
│   ├── status_jobs.py
│   ├── retry_failed_job.py
│   ├── repair_gold_from_json.py
│   ├── format_whisperx_json.py
│   ├── project_paths.py
│   └── init_dirs.py
├── tests
│   └── smoke_tests.py
├── tools
│   └── backup_scripts.py
├── config
│   ├── whisperx_config.example.json
│   └── whisperx_config.json
├── docs
│   └── PIPELINE_DETAILS.md
├── hf_cache
├── .env.example
├── .env
├── .gitignore
├── environment.yml
└── README.md
```

---

## Конфигурация

Публичный пример конфига:

```text
config\whisperx_config.example.json
```

Локальный рабочий конфиг:

```text
config\whisperx_config.json
```

Локальный конфиг не коммитится.

Пример:

```json
{
  "language": "ru",
  "model": "large-v3",
  "device": "cpu",
  "compute_type": "int8",
  "batch_size": 4,
  "threads": 16,
  "align_model": "jonatasgrosman/wav2vec2-large-xlsr-53-russian",
  "diarize": false,
  "min_speakers": null,
  "max_speakers": null,
  "output_format": "json",
  "vad_method": "silero",
  "vad_onset": 0.3,
  "vad_offset": 0.2,
  "hf_cache_dir": "hf_cache"
}
```

---

## Секреты

Публичный пример:

```text
.env.example
```

Локальный файл:

```text
.env
```

Пример локального `.env`:

```env
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`.env` не коммитится.

---

## Что не попадает в Git

В Git не должны попадать:

```text
.env
config/whisperx_config.json
data/landing/*
data/bronze/raw_original/*
data/processing/*
data/silver/asr_json/*
data/silver/audio_flac/*
data/gold/transcripts/*
data/failed/*
hf_cache/*
.local_backups/*
backup*
*.bak
__pycache__
```

В Git попадают только `.gitkeep` для сохранения структуры папок.

---

## Проверки перед commit

```bat
python tests\smoke_tests.py --skip-doctor
git status --short --untracked-files=all
git add --dry-run .
```

Sensitive check:

```bat
findstr /s /n /i "C:\\whisperx_ru C:/whisperx_ru hf_ token secret HUGGINGFACE PYANNOTE_AUTH retry_fail SYNTHETIC" scripts\*.py tests\*.py tools\*.py config\*.json *.md .env.example environment.yml
```

Нормально, если находятся только имена переменных, placeholder-строки и `hf_cache`.

Ненормально, если находятся:

* реальный `hf_...` token;
* реальные имена встреч;
* реальные job_id;
* реальные JSON/FLAC/TXT результаты;
* локальные абсолютные пути, зашитые в код.

---

## Roadmap

Ближайшие шаги:

1. `check_storage_integrity.py` — проверка целостности файлового хранилища;
2. интеграция краткой integrity-сводки в `pipeline.py doctor`;
3. `recover_orphaned_processing.py` — безопасное восстановление orphaned processing;
4. `cleanup_old_jobs.py` — безопасная очистка временных/старых данных через dry-run;
5. GitHub Actions для `tests/smoke_tests.py --skip-doctor`;
6. `jobs.db` как индекс для будущего UI;
7. UI/API слой;
8. Airflow как оркестратор уже готовых CLI-команд.

Принцип развития:

```text
Не добавлять новые обязательные ручные шаги.
Добавлять внутренние инструменты.
Оборачивать их в единый CLI или будущий UI.
Сначала устойчивость и наблюдаемость, потом DB/UI/Airflow.
```
