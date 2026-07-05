# Local WhisperX Meeting Pipeline

Локальный файловый пайплайн для обработки аудио/видео записей встреч через WhisperX.

Проект не заменяет WhisperX, а добавляет вокруг него практичную локальную обвязку:

* приём файлов в `data/landing`;
* сохранение оригиналов в `bronze`;
* подготовка рабочего WAV;
* сохранение архивного FLAC;
* запуск WhisperX;
* сохранение raw JSON;
* публикация результата в `gold`;
* retry / repair после ошибок;
* проверка состояния локального хранилища.

Текущий статус: **локальный MVP без Airflow, веб-интерфейса, SFTP и облачной обработки**.

Подробности по устройству пайплайна: [`docs/PIPELINE_DETAILS.md`](docs/PIPELINE_DETAILS.md).

---

## Возможности MVP

* обработка одного файла;
* пакетная обработка файлов из `data/landing`;
* сохранение исходников в `data/bronze/raw_original`;
* сохранение FLAC-архива в `data/silver/audio_flac`;
* сохранение raw WhisperX JSON в `data/silver/asr_json`;
* публикация результата в `data/gold/transcripts`;
* read-only аудит состояния jobs;
* полный retry failed job от `bronze`;
* быстрый repair `gold` из готового `silver/asr_json`;
* защита от случайного коммита данных, токенов и кешей моделей.

---

## Не входит в MVP

В текущей версии намеренно нет:

* Airflow;
* веб-интерфейса;
* SFTP / upload;
* watcher / service mode;
* MinIO / S3;
* облачной обработки;
* VTT / WebVTT;
* веб-плеера;
* сложного speaker remapping;
* оптимизации скорости диаризации.

---

## Требования

Проверенная базовая среда:

* Windows 11;
* conda;
* установленный `ffmpeg`;
* установленный и рабочий WhisperX;
* Hugging Face token, если используется diarization / pyannote.

В примерах ниже `<PROJECT_ROOT>` — папка, куда склонирован проект.

Например:

```bat
cd /d C:\whisperx_ru
```

---

## Быстрый старт

### 1. Склонировать проект

```bat
cd /d C:\
git clone <REPO_URL> whisperx_ru
cd /d C:\whisperx_ru
```

### 2. Создать окружение

Если есть `environment.yml`:

```bat
conda env create -f environment.yml
conda activate whisperx-ru
```

Если окружение уже создано:

```bat
conda activate whisperx-ru
```

### 3. Создать локальные папки

```bat
python scripts\init_dirs.py
```

### 4. Создать локальный конфиг

```bat
copy config\whisperx_config.example.json config\whisperx_config.json
```

Файл `config/whisperx_config.json` локальный и не должен попадать в Git.

### 5. Создать локальный `.env`

```bat
copy .env.example .env
```

Если diarization не используется:

```env
HF_TOKEN=
```

Если diarization включена:

```env
HF_TOKEN=your_huggingface_token_here
```

Файл `.env` локальный и не должен попадать в Git.

### 6. Положить файл в landing

```text
data\landing\meeting.m4a
```

### 7. Проверить dry-run

```bat
python scripts\process_landing_once.py --dry-run --show-sizes
```

### 8. Запустить обработку

Один файл:

```bat
python scripts\process_one_file.py --input data\landing\meeting.m4a
```

Все готовые файлы из `landing`:

```bat
python scripts\process_landing_once.py
```

### 9. Проверить состояние

```bat
python scripts\status_jobs.py
```

---

## Структура проекта

```text
<PROJECT_ROOT>
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
│   ├── init_dirs.py
│   ├── project_paths.py
│   ├── run_whisperx.py
│   ├── process_one_file.py
│   ├── process_landing_once.py
│   ├── status_jobs.py
│   ├── retry_failed_job.py
│   ├── repair_gold_from_json.py
│   └── format_whisperx_json.py
├── config
│   └── whisperx_config.example.json
├── docs
│   └── PIPELINE_DETAILS.md
├── hf_cache
├── .env.example
├── .gitignore
└── README.md
```

---

## Основные команды

### Обработка одного файла

```bat
python scripts\process_one_file.py --input data\landing\meeting.m4a
```

### Обработка всех файлов из landing

```bat
python scripts\process_landing_once.py
```

### Проверка состояния

```bat
python scripts\status_jobs.py
```

### Полный retry от bronze

```bat
python scripts\retry_failed_job.py --job-id <failed_job_id>
```

### Быстрый repair из silver JSON

```bat
python scripts\repair_gold_from_json.py --job-id <failed_job_id>
```

---

## Параметры скриптов

### `process_one_file.py`

Главный пайплайн обработки одного файла.

```bat
python scripts\process_one_file.py --input <path>
```

| Параметр                     | Назначение                                                                                                           |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `--input <path>`             | Входной аудио/видео файл. Обычно файл из `data/landing`.                                                             |
| `--keep-processing`          | Не удалять `data/processing/<job_id>` после success. Полезно для отладки.                                            |
| `--from-bronze`              | Использовать уже принятый файл из `bronze`, не переносить его заново. Обычно вызывается через `retry_failed_job.py`. |
| `--original-filename <name>` | Исходное пользовательское имя файла для retry/from-bronze режима.                                                    |
| `--retry-of-job-id <job_id>` | ID failed job, от которой создаётся новая попытка.                                                                   |

Обычный пользовательский запуск:

```bat
python scripts\process_one_file.py --input data\landing\meeting.m4a
```

---

### `process_landing_once.py`

Однократная пакетная обработка файлов из `data/landing`.

```bat
python scripts\process_landing_once.py
```

| Параметр              | Назначение                                                           |
| --------------------- | -------------------------------------------------------------------- |
| `--dry-run`           | Только показать, какие файлы будут обработаны.                       |
| `--show-sizes`        | Показать размеры файлов в списке.                                    |
| `--limit N`           | Обработать максимум `N` файлов.                                      |
| `--newest-first`      | Обрабатывать сначала самые новые файлы. По умолчанию сначала старые. |
| `--continue-on-error` | Не останавливаться на первой ошибке.                                 |
| `--keep-processing`   | Передать `--keep-processing` в `process_one_file.py`.                |

Примеры:

```bat
python scripts\process_landing_once.py --dry-run --show-sizes
python scripts\process_landing_once.py --limit 2
python scripts\process_landing_once.py --continue-on-error
```

---

### `status_jobs.py`

Read-only аудит состояния локального хранилища.

```bat
python scripts\status_jobs.py
```

| Параметр            | Назначение                                                               |
| ------------------- | ------------------------------------------------------------------------ |
| `--limit N`         | Сколько элементов показывать в каждой секции.                            |
| `--sizes`           | Посчитать размеры файлов/папок. Может быть медленнее на больших архивах. |
| `--orphan-hours N`  | Через сколько часов `processing` считать кандидатом в orphan.            |
| `--json`            | Вывести полный отчёт в JSON.                                             |
| `--landing-only`    | Показать только `landing`.                                               |
| `--processing-only` | Показать только `processing`.                                            |
| `--failed-only`     | Показать только `failed`.                                                |
| `--gold-only`       | Показать только `gold`.                                                  |

Примеры:

```bat
python scripts\status_jobs.py
python scripts\status_jobs.py --failed-only
python scripts\status_jobs.py --gold-only
python scripts\status_jobs.py --json
```

---

### `retry_failed_job.py`

Полный retry failed job от исходника в `bronze`.

```bat
python scripts\retry_failed_job.py --job-id <failed_job_id>
```

| Параметр              | Назначение                                                |
| --------------------- | --------------------------------------------------------- |
| `--job-id <job_id>`   | ID failed job из `data/failed/<job_id>`.                  |
| `--failed-dir <path>` | Прямой путь к папке failed job.                           |
| `--keep-processing`   | Сохранить `processing` после успешного retry для отладки. |

Используется, если WhisperX не дошёл до готового JSON.

---

### `repair_gold_from_json.py`

Быстрый repair `gold` из уже готового `silver/asr_json`.

```bat
python scripts\repair_gold_from_json.py --job-id <failed_job_id>
```

| Параметр                     | Назначение                                             |
| ---------------------------- | ------------------------------------------------------ |
| `--job-id <job_id>`          | ID failed job из `data/failed/<job_id>`.               |
| `--failed-dir <path>`        | Прямой путь к папке failed job.                        |
| `--json <path>`              | Прямой путь к `silver/asr_json/*.json`.                |
| `--original-filename <name>` | Исходное имя файла. Нужно при запуске через `--json`.  |
| `--no-mark-retried`          | Не создавать `RETRIED_SUCCESSFULLY.json` в failed job. |

Примеры:

```bat
python scripts\repair_gold_from_json.py --job-id <failed_job_id>

python scripts\repair_gold_from_json.py ^
  --json data\silver\asr_json\<job_id>.json ^
  --original-filename meeting.m4a
```

Используется, если WhisperX уже создал JSON, но `gold` отсутствует или некорректен.

---

### `run_whisperx.py`

Wrapper над WhisperX.

Обычно вызывается из `process_one_file.py`.

```bat
python scripts\run_whisperx.py <input_file>
```

| Параметр              | Назначение                                                       |
| --------------------- | ---------------------------------------------------------------- |
| `input_file`          | Входной файл или путь к нему.                                    |
| `--config <path>`     | Путь к JSON-конфигу. По умолчанию `config/whisperx_config.json`. |
| `--output-dir <path>` | Папка для технического вывода WhisperX.                          |

Обычному пользователю чаще не нужно запускать этот скрипт напрямую.

---

### `init_dirs.py`

Создаёт локальную структуру папок.

```bat
python scripts\init_dirs.py
```

Параметров нет.

---

### `format_whisperx_json.py`

Вспомогательный форматтер TXT/MD из WhisperX JSON.

В основном пайплайне сейчас не вызывается автоматически, потому что `gold` хранит raw JSON с word-level таймингами.

Параметры зависят от текущей версии скрипта:

```bat
python scripts\format_whisperx_json.py --help
```

---

## Конфигурация

Публичный example-конфиг:

```text
config/whisperx_config.example.json
```

Локальный рабочий конфиг:

```text
config/whisperx_config.json
```

Создать локальный конфиг:

```bat
copy config\whisperx_config.example.json config\whisperx_config.json
```

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

`hf_cache_dir` можно указывать относительным путём. Тогда он будет считаться от корня проекта.

---

## Секреты и данные

Не коммитьте:

* реальные аудио/видео записи;
* raw WhisperX JSON;
* результаты `gold`;
* failed jobs;
* логи;
* Hugging Face cache;
* Hugging Face token;
* локальный `.env`;
* локальный `config/whisperx_config.json`.

В репозитории должны быть только:

* код;
* example-конфиги;
* `.gitkeep`;
* документация;
* `.env.example`;
* `.gitignore`.

Перед commit полезно проверить:

```bat
git status --short --untracked-files=all
git add --dry-run .
```

---

## Roadmap

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

Airflow в будущей архитектуре должен быть оркестратором, а не хранилищем больших файлов.
