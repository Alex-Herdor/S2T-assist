from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

DATA_DIR = BASE_DIR / "data"
FAILED_DIR = DATA_DIR / "failed"
SILVER_JSON_DIR = DATA_DIR / "silver" / "asr_json"
GOLD_TRANSCRIPTS_DIR = DATA_DIR / "gold" / "transcripts"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_stem(name: str, max_len: int = 80) -> str:
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


def make_repair_job_id(original_filename: str, json_path: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{safe_stem(original_filename)}_{short_hash(json_path)}"


def make_result_dir_name(original_filename: str, job_id: str) -> str:
    original_stem = safe_stem(original_filename, max_len=80)

    timestamp_match = re.match(r"^(\d{8}_\d{6})_", job_id)
    timestamp = timestamp_match.group(1) if timestamp_match else datetime.now().strftime("%Y%m%d_%H%M%S")

    hash_part = job_id.rsplit("_", 1)[-1] if "_" in job_id else "nohash"

    return f"{original_stem}__{timestamp}__{hash_part}"


def make_gold_result_dir(original_filename: str, job_id: str) -> Path:
    GOLD_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    result_dir_name = make_result_dir_name(
        original_filename=original_filename,
        job_id=job_id,
    )

    result_dir = GOLD_TRANSCRIPTS_DIR / result_dir_name

    if not result_dir.exists():
        result_dir.mkdir(parents=True, exist_ok=False)
        return result_dir

    suffix = datetime.now().strftime("%f")
    result_dir = GOLD_TRANSCRIPTS_DIR / f"{result_dir_name}__{suffix}"
    result_dir.mkdir(parents=True, exist_ok=False)

    return result_dir


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError(f"Ожидался JSON object, а не {type(data)}: {path}")

    return data


def safe_read_json(path: Path) -> dict:
    try:
        return read_json(path)
    except Exception:
        return {}


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


def resolve_failed_dir(job_id: str | None, failed_dir: str | None) -> Path | None:
    if failed_dir:
        path = Path(failed_dir).resolve()
    elif job_id:
        path = FAILED_DIR / job_id
    else:
        return None

    if not path.exists():
        raise FileNotFoundError(f"Failed job не найден: {path}")

    if not path.is_dir():
        raise RuntimeError(f"Это не папка failed job: {path}")

    return path


def find_context_path(failed_job_dir: Path) -> Path:
    candidates = [
        failed_job_dir / "processing" / "job_context.json",
        failed_job_dir / "job_context.json",
    ]

    for path in candidates:
        if path.exists():
            return path

    raise FileNotFoundError(
        f"Не найден job_context.json для failed job: {failed_job_dir}"
    )


def resolve_silver_json_from_failed(context: dict, old_job_id: str) -> Path:
    paths = context.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}

    silver_value = paths.get("silver_asr_json")

    if silver_value:
        silver_path = Path(silver_value)

        if silver_path.exists():
            return silver_path.resolve()

        print(f"WARNING: silver_asr_json из job_context не найден: {silver_path}")

    fallback = SILVER_JSON_DIR / f"{old_job_id}.json"

    if fallback.exists():
        print(f"Найден silver fallback по old_job_id: {fallback}")
        return fallback.resolve()

    raise FileNotFoundError(
        "Не найден silver ASR JSON для repair. "
        f"old_job_id={old_job_id}"
    )


def load_gold_context(result_dir: Path) -> dict:
    context_path = result_dir / "job_context.json"
    manifest_path = result_dir / "manifest.json"

    if context_path.exists():
        return safe_read_json(context_path)

    if manifest_path.exists():
        return safe_read_json(manifest_path)

    return {}


def find_successful_repairs(old_job_id: str) -> list[dict]:
    repairs: list[dict] = []

    if not GOLD_TRANSCRIPTS_DIR.exists():
        return repairs

    for result_dir in GOLD_TRANSCRIPTS_DIR.iterdir():
        if not result_dir.is_dir():
            continue

        context = load_gold_context(result_dir)

        if context.get("retry_of_job_id") != old_job_id:
            continue

        if context.get("status") != "success":
            continue

        paths = context.get("paths", {})
        if not isinstance(paths, dict):
            paths = {}

        repairs.append(
            {
                "new_job_id": context.get("job_id"),
                "original_filename": context.get("original_filename"),
                "attempt_type": context.get("attempt_type"),
                "source_mode": context.get("source_mode"),
                "retry_of_job_id": context.get("retry_of_job_id"),
                "gold_result_dir": str(result_dir),
                "gold_whisperx_raw_json": paths.get("gold_whisperx_raw_json"),
                "gold_manifest": paths.get("gold_manifest"),
                "gold_job_context": paths.get("gold_job_context"),
                "finished_at": context.get("updated_at") or context.get("finished_at"),
                "mtime": result_dir.stat().st_mtime,
            }
        )

    return sorted(repairs, key=lambda x: x.get("mtime") or 0, reverse=True)


def write_manifest(
    path: Path,
    job_id: str,
    original_filename: str,
    silver_json_path: Path,
    gold_raw_json_path: Path,
    result_dir: Path,
    raw_json_info: dict,
    old_job_id: str | None,
) -> None:
    manifest = {
        "job_id": job_id,
        "status": "success",
        "source_mode": "silver_asr_json",
        "attempt_type": "repair_from_silver",
        "retry_of_job_id": old_job_id,
        "original_filename": original_filename,
        "result_dir": str(result_dir),
        "result_type": "whisperx_raw_json",
        "raw_json_info": raw_json_info,
        "paths": {
            "silver_asr_json": str(silver_json_path),
            "gold_result_dir": str(result_dir),
            "gold_whisperx_raw_json": str(gold_raw_json_path),
            "gold_manifest": str(path),
        },
        "finished_at": now_iso(),
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def write_job_context(
    path: Path,
    job_id: str,
    original_filename: str,
    silver_json_path: Path,
    gold_raw_json_path: Path,
    manifest_path: Path,
    result_dir: Path,
    raw_json_info: dict,
    old_job_id: str | None,
    failed_dir: Path | None,
) -> None:
    context = {
        "job_id": job_id,
        "status": "success",
        "current_step": "gold_ready",
        "failed_step": None,
        "error_message": None,
        "source_mode": "silver_asr_json",
        "attempt_type": "repair_from_silver",
        "retry_of_job_id": old_job_id,
        "original_filename": original_filename,
        "raw_json_info": raw_json_info,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "paths": {
            "silver_asr_json": str(silver_json_path),
            "gold_result_dir": str(result_dir),
            "gold_whisperx_raw_json": str(gold_raw_json_path),
            "gold_manifest": str(manifest_path),
            "gold_job_context": str(path),
            "failed_dir": str(failed_dir) if failed_dir else None,
        },
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(context, f, ensure_ascii=False, indent=2)


def repair_gold_from_json(
    silver_json_path: Path,
    original_filename: str,
    old_job_id: str | None,
    failed_dir: Path | None,
) -> dict:
    if not silver_json_path.exists():
        raise FileNotFoundError(f"Silver JSON не найден: {silver_json_path}")

    job_id = make_repair_job_id(
        original_filename=original_filename,
        json_path=silver_json_path,
    )

    result_dir = make_gold_result_dir(
        original_filename=original_filename,
        job_id=job_id,
    )

    gold_raw_json_path = result_dir / "whisperx_raw.json"
    manifest_path = result_dir / "manifest.json"
    job_context_path = result_dir / "job_context.json"

    shutil.copy2(silver_json_path, gold_raw_json_path)

    raw_json_info = inspect_whisperx_json(gold_raw_json_path)

    if not raw_json_info.get("has_word_timings"):
        print("")
        print("WARNING: в WhisperX JSON не найдены word-level тайминги.")
        print("Следующая модель может работать хуже или не сможет уточнять реплики по словам.")
        print("")

    write_manifest(
        path=manifest_path,
        job_id=job_id,
        original_filename=original_filename,
        silver_json_path=silver_json_path,
        gold_raw_json_path=gold_raw_json_path,
        result_dir=result_dir,
        raw_json_info=raw_json_info,
        old_job_id=old_job_id,
    )

    write_job_context(
        path=job_context_path,
        job_id=job_id,
        original_filename=original_filename,
        silver_json_path=silver_json_path,
        gold_raw_json_path=gold_raw_json_path,
        manifest_path=manifest_path,
        result_dir=result_dir,
        raw_json_info=raw_json_info,
        old_job_id=old_job_id,
        failed_dir=failed_dir,
    )

    return {
        "job_id": job_id,
        "result_dir": result_dir,
        "gold_raw_json_path": gold_raw_json_path,
        "manifest_path": manifest_path,
        "job_context_path": job_context_path,
        "raw_json_info": raw_json_info,
    }


def mark_failed_as_retried(
    failed_job_dir: Path,
    old_job_id: str,
    retry_source: str,
) -> Path:
    successful_repairs = find_successful_repairs(old_job_id)

    if not successful_repairs:
        raise RuntimeError(
            "Repair завершился, но успешный gold с "
            f"retry_of_job_id={old_job_id} не найден."
        )

    latest_retry = successful_repairs[0]

    marker = {
        "status": "retried_successfully",
        "old_job_id": old_job_id,
        "retry_source": retry_source,
        "marked_at": now_iso(),
        "latest_retry": latest_retry,
        "all_successful_retries": successful_repairs,
        "notes": (
            "Старый failed не удалён автоматически. "
            "Он сохранён как диагностический слепок исходной ошибки. "
            "Этот retry/repair выполнен из silver ASR JSON без повторного WhisperX."
        ),
    }

    marker_path = failed_job_dir / "RETRIED_SUCCESSFULLY.json"

    with marker_path.open("w", encoding="utf-8") as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)

    return marker_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Восстановление gold из уже готового silver/asr_json без повторного WhisperX."
    )

    parser.add_argument(
        "--job-id",
        type=str,
        default=None,
        help="job_id из data\\failed\\<job_id>. Будет взят silver JSON из job_context или silver\\asr_json.",
    )

    parser.add_argument(
        "--failed-dir",
        type=str,
        default=None,
        help="Полный путь к папке failed job.",
    )

    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Прямой путь к silver/asr_json/*.json. В этом режиме marker failed не создаётся, если не указан --job-id/--failed-dir.",
    )

    parser.add_argument(
        "--original-filename",
        type=str,
        default=None,
        help="Исходное пользовательское имя файла. Нужно при запуске через --json.",
    )

    parser.add_argument(
        "--no-mark-retried",
        action="store_true",
        help="Не создавать RETRIED_SUCCESSFULLY.json в failed job.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    failed_dir = resolve_failed_dir(
        job_id=args.job_id,
        failed_dir=args.failed_dir,
    )

    context = {}
    old_job_id = None
    original_filename = args.original_filename
    silver_json_path: Path

    if failed_dir:
        context_path = find_context_path(failed_dir)
        context = read_json(context_path)

        old_job_id = context.get("job_id") or failed_dir.name
        original_filename = original_filename or context.get("original_filename")

        if not original_filename:
            raise RuntimeError(
                f"В job_context нет original_filename: {context_path}"
            )

        silver_json_path = resolve_silver_json_from_failed(
            context=context,
            old_job_id=old_job_id,
        )
    else:
        if not args.json:
            raise RuntimeError("Нужно указать --job-id / --failed-dir или --json")

        if not original_filename:
            raise RuntimeError("При запуске через --json нужно указать --original-filename")

        silver_json_path = Path(args.json).resolve()

    print("")
    print("=== Repair gold from silver ASR JSON ===")
    print(f"silver json:       {silver_json_path}")
    print(f"original filename: {original_filename}")
    print(f"old failed job:    {old_job_id}")
    print("")

    result = repair_gold_from_json(
        silver_json_path=silver_json_path,
        original_filename=original_filename,
        old_job_id=old_job_id,
        failed_dir=failed_dir,
    )

    marker_path = None

    if failed_dir and old_job_id and not args.no_mark_retried:
        marker_path = mark_failed_as_retried(
            failed_job_dir=failed_dir,
            old_job_id=old_job_id,
            retry_source="silver_asr_json",
        )

    print("")
    print("=== Готово ===")
    print(f"new job_id:    {result['job_id']}")
    print(f"result dir:    {result['result_dir']}")
    print(f"raw json:      {result['gold_raw_json_path']}")
    print(f"manifest:      {result['manifest_path']}")
    print(f"job context:   {result['job_context_path']}")
    print(f"words timings: {result['raw_json_info'].get('words_with_timings', 0)}")

    if marker_path:
        print(f"marker:        {marker_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())