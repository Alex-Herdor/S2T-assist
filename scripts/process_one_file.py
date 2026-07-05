from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import queue
import threading
from datetime import datetime
from pathlib import Path


ALLOWED_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
}

IGNORED_SUFFIXES = {
    ".done", ".uploading", ".part", ".tmp", ".crdownload",
}


def project_root() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name.lower() == "scripts":
        return current.parent.parent
    return current.parent


BASE_DIR = project_root()

DIRS = {
    "landing": BASE_DIR / "data" / "landing",
    "bronze_raw": BASE_DIR / "data" / "bronze" / "raw_original",
    "processing": BASE_DIR / "data" / "processing",
    "audio_flac": BASE_DIR / "data" / "silver" / "audio_flac",
    "asr_json": BASE_DIR / "data" / "silver" / "asr_json",
    "transcripts": BASE_DIR / "data" / "gold" / "transcripts",
    "failed": BASE_DIR / "data" / "failed",
    "hf_cache": BASE_DIR / "hf_cache",
}


def ensure_dirs() -> None:
    for path in DIRS.values():
        path.mkdir(parents=True, exist_ok=True)


def safe_stem(name: str, max_len: int = 70) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-. ]+", "_", stem, flags=re.UNICODE)
    stem = re.sub(r"\s+", "_", stem)
    stem = stem.strip("._- ")
    if not stem:
        stem = "meeting"
    return stem[:max_len]


def short_hash(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:8]


def make_job_id(input_path: Path, original_name: str | None = None) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    display_name = original_name or input_path.name
    return f"{ts}_{safe_stem(display_name)}_{short_hash(input_path)}"

def make_result_dir_name(original_name: str, job_id: str) -> str:
    original_stem = safe_stem(original_name, max_len=80)

    # job_id сейчас выглядит так:
    # 20260705_095001_meeting_2min_ffc1d679
    timestamp_match = re.match(r"^(\d{8}_\d{6})_", job_id)
    timestamp = timestamp_match.group(1) if timestamp_match else datetime.now().strftime("%Y%m%d_%H%M%S")

    hash_part = job_id.rsplit("_", 1)[-1] if "_" in job_id else "nohash"

    return f"{original_stem}__{timestamp}__{hash_part}"


def make_gold_result_dir(original_name: str, job_id: str) -> Path:
    result_dir_name = make_result_dir_name(original_name, job_id)
    result_dir = DIRS["transcripts"] / result_dir_name

    if not result_dir.exists():
        result_dir.mkdir(parents=True, exist_ok=False)
        return result_dir

    # На случай совсем редкой коллизии.
    suffix = datetime.now().strftime("%f")
    result_dir = DIRS["transcripts"] / f"{result_dir_name}__{suffix}"
    result_dir.mkdir(parents=True, exist_ok=False)

    return result_dir

def is_candidate_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("."):
        return False
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return False
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False
    return True


def pick_file_from_landing() -> Path:
    files = [p for p in DIRS["landing"].iterdir() if is_candidate_file(p)]
    if not files:
        raise RuntimeError(
            f"В landing нет подходящих файлов. Положи аудио/видео в: {DIRS['landing']}"
        )
    return sorted(files, key=lambda p: p.stat().st_mtime)[0]


def wait_until_file_stable(path: Path, timeout_sec: int = 30, interval_sec: int = 2) -> None:
    deadline = time.time() + timeout_sec
    previous = None

    while time.time() < deadline:
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime_ns)

        if previous == current and stat.st_size > 0:
            return

        previous = current
        time.sleep(interval_sec)

    raise RuntimeError(
        f"Файл не выглядит стабильным за {timeout_sec} сек: {path}. "
        f"Возможно, он ещё копируется."
    )


def find_script(name: str) -> Path:
    path = BASE_DIR / "scripts" / name
    if path.exists():
        return path
    raise RuntimeError(f"Не найден скрипт {name}: {path}")


def run_command(
    command: list[str],
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
    fail_on_error: bool = True,
    heartbeat_sec: int = 30,
) -> int:
    command_str = " ".join(f'"{x}"' if " " in str(x) else str(x) for x in command)

    log_path.parent.mkdir(parents=True, exist_ok=True)

    def reader_thread(stream, out_queue: queue.Queue[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                out_queue.put(line)
        finally:
            stream.close()

    with log_path.open("a", encoding="utf-8") as log:
        header = (
            "\n" + "=" * 100 + "\n"
            f"$ {command_str}\n"
            f"cwd={cwd}\n"
            + "=" * 100 + "\n"
        )

        print(header, end="")
        log.write(header)
        log.flush()

        started_at = time.time()
        last_output_at = started_at

        process = subprocess.Popen(
            [str(x) for x in command],
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        assert process.stdout is not None

        output_queue: queue.Queue[str] = queue.Queue()
        thread = threading.Thread(
            target=reader_thread,
            args=(process.stdout, output_queue),
            daemon=True,
        )
        thread.start()

        while process.poll() is None or not output_queue.empty():
            try:
                line = output_queue.get(timeout=1)
                print(line, end="")
                log.write(line)
                log.flush()
                last_output_at = time.time()
            except queue.Empty:
                now = time.time()

                if now - last_output_at >= heartbeat_sec:
                    elapsed_min = int((now - started_at) // 60)
                    msg = (
                        f"[still running] Команда ещё выполняется, "
                        f"прошло ~{elapsed_min} мин. "
                        f"Это нормально для WhisperX на CPU.\n"
                    )
                    print(msg, end="")
                    log.write(msg)
                    log.flush()
                    last_output_at = now

        thread.join(timeout=2)

        exit_msg = f"\n[exit_code={process.returncode}]\n"
        print(exit_msg, end="")
        log.write(exit_msg)
        log.flush()

    if fail_on_error and process.returncode != 0:
        raise RuntimeError(f"Команда завершилась с ошибкой {process.returncode}: {command_str}")

    return process.returncode


def ffmpeg_to_wav(input_path: Path, wav_path: Path, log_path: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i", str(input_path),
        "-map", "0:a:0",
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    run_command(command, BASE_DIR, log_path)


def ffmpeg_to_flac(input_path: Path, flac_path: Path, log_path: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i", str(input_path),
        "-map", "0:a:0",
        "-vn",
        "-c:a", "flac",
        "-compression_level", "8",
        str(flac_path),
    ]
    run_command(command, BASE_DIR, log_path)

def list_json_candidates(job_dir: Path) -> list[Path]:
    candidates: list[Path] = []

    if job_dir.exists():
        candidates.extend(job_dir.rglob("*.json"))

    filtered = []
    for path in candidates:
        parts_lower = [part.lower() for part in path.parts]

        if "hf_cache" in parts_lower:
            continue

        if path.name.lower() in {"job_manifest.json"}:
            continue

        filtered.append(path)

    return filtered

    candidates: list[Path] = []

    roots_recursive = [
        job_dir,
        BASE_DIR / "outputs",
        BASE_DIR / "output",
        BASE_DIR / "whisperx_output",
    ]

    roots_direct = [
        BASE_DIR,
        BASE_DIR / "scripts",
    ]

    for root in roots_recursive:
        if root.exists():
            candidates.extend(root.rglob("*.json"))

    for root in roots_direct:
        if root.exists():
            candidates.extend(root.glob("*.json"))

    filtered = []
    for path in candidates:
        parts_lower = [part.lower() for part in path.parts]
        if "hf_cache" in parts_lower:
            continue
        if "config" in parts_lower:
            continue
        if path.name.lower() in {"whisperx_config.json", "job_manifest.json"}:
            continue
        filtered.append(path)

    return filtered


def looks_like_whisperx_json(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and isinstance(data.get("segments"), list):
            return True

        if isinstance(data, list):
            return True

        return False
    except Exception:
        return False


def choose_whisperx_json(candidates: list[Path]) -> Path:
    if not candidates:
        raise RuntimeError("WhisperX JSON не найден после запуска run_whisperx.py")

    whisperx_like = [p for p in candidates if looks_like_whisperx_json(p)]
    pool = whisperx_like if whisperx_like else candidates

    return max(pool, key=lambda p: (p.stat().st_size, p.stat().st_mtime))


def run_whisperx(wav_path: Path, job_dir: Path, log_path: Path) -> Path:
    run_script = find_script("run_whisperx.py")

    before = {p.resolve() for p in list_json_candidates(job_dir)}
    started_at = time.time()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HOME"] = str(DIRS["hf_cache"])
    env["HF_HUB_CACHE"] = str(DIRS["hf_cache"])
    env["TRANSFORMERS_CACHE"] = str(DIRS["hf_cache"])

    whisperx_output_dir = job_dir / "whisperx_output"

    command = [
        sys.executable,
        str(run_script),
        str(wav_path),
        "--config",
        str(BASE_DIR / "config" / "whisperx_config.json"),
        "--output-dir",
        str(whisperx_output_dir),
    ]

    run_command(command, job_dir, log_path, env=env)

    after = list_json_candidates(job_dir)

    new_files = [
        p for p in after
        if p.resolve() not in before
    ]

    if not new_files:
        new_files = [
            p for p in after
            if p.stat().st_mtime >= started_at - 5
        ]

    return choose_whisperx_json(new_files)


def write_manifest(
    path: Path,
    job_id: str,
    original_filename: str,
    bronze_path: Path,
    flac_path: Path,
    silver_json_path: Path,
    gold_raw_json_path: Path,
    result_dir: Path,
    raw_json_info: dict,
    source_mode: str,
    attempt_type: str,
    retry_of_job_id: str | None,
) -> None:
    manifest = {
        "job_id": job_id,
        "status": "success",
        "source_mode": source_mode,
        "attempt_type": attempt_type,
        "retry_of_job_id": retry_of_job_id,
        "original_filename": original_filename,
        "result_dir": str(result_dir),
        "result_type": "whisperx_raw_json",
        "raw_json_info": raw_json_info,
        "paths": {
            "bronze_raw_original": str(bronze_path),
            "silver_audio_flac": str(flac_path),
            "silver_asr_json": str(silver_json_path),
            "gold_result_dir": str(result_dir),
            "gold_whisperx_raw_json": str(gold_raw_json_path),
        },
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def move_job_to_failed(job_dir: Path, job_id: str, error_text: str) -> Path:
    failed_dir = DIRS["failed"] / job_id

    if failed_dir.exists():
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_dir = DIRS["failed"] / f"{job_id}_{suffix}"

    failed_dir.mkdir(parents=True, exist_ok=True)

    error_path = failed_dir / "ERROR.txt"
    error_path.write_text(error_text, encoding="utf-8")

    if job_dir.exists():
        context = read_job_context(job_dir)
        failed_step = context.get("current_step") or "unknown"

        update_job_context(
            job_dir,
            status="failed",
            failed_step=failed_step,
            error_message=error_text.splitlines()[-1] if error_text else None,
            error_traceback=error_text,
        )

        destination = failed_dir / "processing"
        shutil.move(str(job_dir), str(destination))

        moved_context_path = destination / "job_context.json"

        if moved_context_path.exists():
            try:
                with moved_context_path.open("r", encoding="utf-8") as f:
                    moved_context = json.load(f)

                paths = moved_context.get("paths", {})
                if not isinstance(paths, dict):
                    paths = {}

                paths["failed_dir"] = str(failed_dir)
                paths["failed_processing_dir"] = str(destination)
                paths["processing_dir_before_failed"] = str(job_dir)

                moved_context["paths"] = paths
                moved_context["updated_at"] = now_iso()

                with moved_context_path.open("w", encoding="utf-8") as f:
                    json.dump(moved_context, f, ensure_ascii=False, indent=2)

            except Exception:
                pass

    return failed_dir


def cleanup_processing_dir(job_dir: Path, log_path: Path, retries: int = 3, delay_sec: int = 2) -> bool:
    if not job_dir.exists():
        print(f"Processing уже отсутствует: {job_dir}")
        return True

    print(f"Очистка processing: {job_dir}")

    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            shutil.rmtree(job_dir)
            print(f"Processing удалён: {job_dir}")
            return True
        except Exception as error:
            last_error = error
            print(f"WARNING: не удалось удалить processing, попытка {attempt}/{retries}")
            print(f"Причина: {repr(error)}")

            try:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n" + "=" * 100 + "\n")
                    log.write(f"CLEANUP WARNING attempt {attempt}/{retries}\n")
                    log.write(f"Не удалось удалить processing: {job_dir}\n")
                    log.write(f"Причина: {repr(error)}\n")
                    log.write("=" * 100 + "\n")
            except Exception:
                pass

            if attempt < retries:
                time.sleep(delay_sec)

    marker_path = job_dir / "CLEANUP_FAILED.txt"

    try:
        remaining_files = []
        for path in job_dir.rglob("*"):
            if path.is_file():
                try:
                    size_mb = path.stat().st_size / 1024 / 1024
                    remaining_files.append((size_mb, path))
                except Exception:
                    remaining_files.append((0, path))

        remaining_files = sorted(remaining_files, reverse=True)[:50]

        marker_lines = [
            "Processing не был удалён после успешной обработки.",
            "",
            "Это НЕ означает, что транскрибация упала.",
            "Gold-результаты уже должны быть созданы.",
            "",
            f"Папка: {job_dir}",
            f"Последняя ошибка очистки: {repr(last_error)}",
            "",
            "Частые причины на Windows:",
            "- файл ещё удерживается процессом python/whisperx/ffmpeg;",
            "- файл открыт в проводнике, редакторе или медиаплеере;",
            "- антивирус временно сканирует файл;",
            "- нет прав на удаление;",
            "",
            "Что сделать:",
            "1. Убедиться, что обработка завершилась.",
            "2. Закрыть программы, которые могут держать файлы.",
            "3. Удалить папку вручную:",
            f"   rmdir /S /Q \"{job_dir}\"",
            "",
            "Крупнейшие оставшиеся файлы:",
        ]

        for size_mb, path in remaining_files:
            marker_lines.append(f"- {size_mb:.1f} MB | {path}")

        marker_path.write_text("\n".join(marker_lines), encoding="utf-8")

        print(f"Создан маркер ошибки очистки: {marker_path}")

    except Exception as marker_error:
        print(f"WARNING: не удалось создать CLEANUP_FAILED.txt: {repr(marker_error)}")

    print("")
    print("WARNING: обработка завершилась успешно, но processing не удалён.")
    print(f"Папка оставлена для ручной проверки: {job_dir}")
    print("Это не failed job, а проблема cleanup.")

    return False


def inspect_whisperx_json(json_path: Path) -> dict:
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        segments = data.get("segments", [])
    elif isinstance(data, list):
        segments = data
    else:
        segments = []

    segment_count = 0
    word_count = 0
    words_with_timings = 0

    for segment in segments:
        if not isinstance(segment, dict):
            continue

        segment_count += 1

        words = segment.get("words", [])
        if not isinstance(words, list):
            continue

        for word in words:
            if not isinstance(word, dict):
                continue

            word_count += 1

            if "start" in word and "end" in word:
                words_with_timings += 1

    return {
        "segment_count": segment_count,
        "word_count": word_count,
        "words_with_timings": words_with_timings,
        "has_word_timings": words_with_timings > 0,
    }


def copy_raw_json_to_gold(
    json_path: Path,
    job_id: str,
    original_name: str,
) -> tuple[Path, Path, dict]:
    result_dir = make_gold_result_dir(original_name=original_name, job_id=job_id)

    gold_raw_json_path = result_dir / "whisperx_raw.json"
    shutil.copy2(json_path, gold_raw_json_path)

    raw_json_info = inspect_whisperx_json(gold_raw_json_path)

    return result_dir, gold_raw_json_path, raw_json_info


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def job_context_path(job_dir: Path) -> Path:
    return job_dir / "job_context.json"


def read_job_context(job_dir: Path) -> dict:
    path = job_context_path(job_dir)

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_job_context(job_dir: Path, context: dict) -> Path:
    path = job_context_path(job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    context["updated_at"] = now_iso()

    tmp_path = path.with_suffix(".json.tmp")

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(context, f, ensure_ascii=False, indent=2)

    tmp_path.replace(path)

    return path


def update_job_context(job_dir: Path, **updates) -> Path:
    context = read_job_context(job_dir)
    context.update(updates)
    return write_job_context(job_dir, context)


def create_job_context(
    job_dir: Path,
    job_id: str,
    original_filename: str,
    input_path: Path,
    bronze_path: Path,
    work_wav_path: Path,
    flac_path: Path,
    silver_json_path: Path,
    log_path: Path,
    source_mode: str,
    attempt_type: str,
    retry_of_job_id: str | None,
) -> Path:
    context = {
        "job_id": job_id,
        "status": "processing",
        "current_step": "job_created",
        "failed_step": None,
        "error_message": None,
        "original_filename": original_filename,
        "source_mode": source_mode,
        "attempt_type": attempt_type,
        "retry_of_job_id": retry_of_job_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "paths": {
            "input_path_at_start": str(input_path),
            "processing_dir": str(job_dir),
            "bronze_raw_original": str(bronze_path),
            "work_wav": str(work_wav_path),
            "silver_audio_flac": str(flac_path),
            "silver_asr_json": str(silver_json_path),
            "process_log": str(log_path),
            "gold_result_dir": None,
            "gold_whisperx_raw_json": None,
            "gold_manifest": None,
            "failed_dir": None,
            "failed_processing_dir": None,
        },
    }

    return write_job_context(job_dir, context)


def update_job_context_paths(job_dir: Path, **path_updates) -> Path:
    context = read_job_context(job_dir)
    paths = context.get("paths", {})

    if not isinstance(paths, dict):
        paths = {}

    for key, value in path_updates.items():
        paths[key] = str(value) if value is not None else None

    context["paths"] = paths

    return write_job_context(job_dir, context)


def process_one(
    input_path: Path | None,
    keep_processing: bool = False,
    from_bronze: bool = False,
    original_filename: str | None = None,
    retry_of_job_id: str | None = None,
) -> int:
    ensure_dirs()

    if input_path is None:
        input_path = pick_file_from_landing()
    else:
        input_path = input_path.resolve()

    if not input_path.exists():
        raise RuntimeError(f"Файл не найден: {input_path}")

    if not is_candidate_file(input_path):
        raise RuntimeError(f"Неподдерживаемый или временный файл: {input_path}")

    print(f"[1/8] Входной файл: {input_path}")
    wait_until_file_stable(input_path)

    source_mode = "bronze" if from_bronze else "landing"
    attempt_type = "retry" if retry_of_job_id else "initial"

    if from_bronze:
        original_name = original_filename or input_path.name
    else:
        original_name = input_path.name

    original_stem = safe_stem(original_name)

    job_id = make_job_id(input_path, original_name=original_name)

    job_dir = DIRS["processing"] / job_id
    job_dir.mkdir(parents=True, exist_ok=False)

    logs_dir = job_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_path = logs_dir / "process.log"

    if from_bronze:
        bronze_path = input_path
    else:
        bronze_path = DIRS["bronze_raw"] / f"{job_id}{input_path.suffix.lower()}"

    wav_path = job_dir / f"{original_stem}__work.wav"
    flac_path = DIRS["audio_flac"] / f"{job_id}.flac"
    final_json = DIRS["asr_json"] / f"{job_id}.json"

    create_job_context(
        job_dir=job_dir,
        job_id=job_id,
        original_filename=original_name,
        input_path=input_path,
        bronze_path=bronze_path,
        work_wav_path=wav_path,
        flac_path=flac_path,
        silver_json_path=final_json,
        log_path=log_path,
        source_mode=source_mode,
        attempt_type=attempt_type,
        retry_of_job_id=retry_of_job_id,
    )

    try:
        if from_bronze:
            update_job_context(job_dir, current_step="use_existing_bronze")

            print(f"[2/8] Использование уже принятого исходника из bronze: {bronze_path}")

            if not bronze_path.exists():
                raise RuntimeError(f"Bronze-исходник не найден: {bronze_path}")
        else:
            update_job_context(job_dir, current_step="move_to_bronze")

            print(f"[2/8] Перенос исходника в bronze: {bronze_path}")
            shutil.move(str(input_path), str(bronze_path))

        original_stem = safe_stem(original_name)

        update_job_context(job_dir, current_step="create_work_wav")
        print(f"[3/8] Создание WAV для WhisperX: {wav_path}")
        ffmpeg_to_wav(bronze_path, wav_path, log_path)

        update_job_context(job_dir, current_step="create_archive_flac")
        print(f"[4/8] Создание FLAC-архива: {flac_path}")
        ffmpeg_to_flac(bronze_path, flac_path, log_path)

        update_job_context(job_dir, current_step="run_whisperx")
        print("[5/8] Запуск WhisperX")
        produced_json = run_whisperx(wav_path, job_dir, log_path)

        update_job_context(job_dir, current_step="save_silver_asr_json")
        print(f"[6/8] Сохранение WhisperX JSON: {final_json}")
        shutil.copy2(produced_json, final_json)

        update_job_context(job_dir, current_step="save_gold_raw_json")
        print("[7/8] Сохранение raw WhisperX JSON в gold")

        result_dir, gold_raw_json_path, raw_json_info = copy_raw_json_to_gold(
            json_path=final_json,
            job_id=job_id,
            original_name=original_name,
        )
        
        update_job_context_paths(
            job_dir,
            gold_result_dir=result_dir,
            gold_whisperx_raw_json=gold_raw_json_path,
        )

        if not raw_json_info.get("has_word_timings"):
            print("")
            print("WARNING: в WhisperX JSON не найдены word-level тайминги.")
            print("Следующая модель может работать хуже или не сможет уточнять реплики по словам.")
            print("Проверь, что alignment реально выполняется и JSON содержит segments[].words[].")
            print("")

        manifest_path = result_dir / "manifest.json"
        write_manifest(
            path=manifest_path,
            job_id=job_id,
            original_filename=original_name,
            bronze_path=bronze_path,
            flac_path=flac_path,
            silver_json_path=final_json,
            gold_raw_json_path=gold_raw_json_path,
            result_dir=result_dir,
            raw_json_info=raw_json_info,
            source_mode=source_mode,
            attempt_type=attempt_type,
            retry_of_job_id=retry_of_job_id,
        )
        
        update_job_context_paths(
            job_dir,
            gold_manifest=manifest_path,
        )

        update_job_context(
            job_dir,
            current_step="gold_ready",
            status="success",
            raw_json_info=raw_json_info,
        )
        
        gold_job_context_path = result_dir / "job_context.json"
        shutil.copy2(job_context_path(job_dir), gold_job_context_path)

        update_job_context_paths(
            job_dir,
            gold_job_context=gold_job_context_path,
        )

        shutil.copy2(job_context_path(job_dir), gold_job_context_path)

        if keep_processing:
            print(f"[8/8] Успех. Processing оставлен: {job_dir}")
        else:
            print("[8/8] Успех. Очистка processing")
            cleanup_processing_dir(job_dir=job_dir, log_path=log_path)

        print("")
        print("Готово:")
        print(f"  job_id: {job_id}")
        print(f"  original: {bronze_path}")
        print(f"  flac:     {flac_path}")
        print(f"  json:     {final_json}")
        print(f"  result:   {result_dir}")
        print(f"  raw json: {gold_raw_json_path}")
        print(f"  manifest: {manifest_path}")
        print(f"  words:    {raw_json_info.get('words_with_timings', 0)} with timings")

        return 0

    except Exception:
        error_text = traceback.format_exc()
        failed_dir = move_job_to_failed(job_dir, job_id, error_text)

        print("")
        print("ОШИБКА обработки.")
        print(f"job_id: {job_id}")
        print(f"Данные ошибки перенесены в: {failed_dir}")
        print("Подробности смотри в ERROR.txt и logs\\process.log")

        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Локальный MVP-пайплайн обработки одного аудио/видео файла через WhisperX."
    )

    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default=None,
        help="Путь к файлу. Если не указан, берётся самый старый подходящий файл из data\\landing.",
    )

    parser.add_argument(
        "--keep-processing",
        action="store_true",
        help="Не удалять data\\processing\\<job_id> после успешной обработки.",
    )
    
    parser.add_argument(
        "--from-bronze",
        action="store_true",
        help="Обрабатывать уже принятый файл из data\\bronze\\raw_original без переноса в bronze.",
    )

    parser.add_argument(
        "--original-filename",
        type=str,
        default=None,
        help="Исходное пользовательское имя файла. Нужно для retry от bronze.",
    )

    parser.add_argument(
        "--retry-of-job-id",
        type=str,
        default=None,
        help="job_id исходной failed-попытки, если это retry.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input) if args.input else None
    return process_one(
        input_path=input_path,
        keep_processing=args.keep_processing,
        from_bronze=args.from_bronze,
        original_filename=args.original_filename,
        retry_of_job_id=args.retry_of_job_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())