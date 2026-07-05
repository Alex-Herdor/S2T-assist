from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

FAILED_DIR = BASE_DIR / "data" / "failed"
BRONZE_DIR = BASE_DIR / "data" / "bronze" / "raw_original"
GOLD_TRANSCRIPTS_DIR = BASE_DIR / "data" / "gold" / "transcripts"
PROCESS_ONE_SCRIPT = SCRIPT_DIR / "process_one_file.py"


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def safe_read_json(path: Path) -> dict:
    try:
        return read_json(path)
    except Exception:
        return {}


def load_gold_context(result_dir: Path) -> dict:
    context_path = result_dir / "job_context.json"
    manifest_path = result_dir / "manifest.json"

    if context_path.exists():
        return safe_read_json(context_path)

    if manifest_path.exists():
        return safe_read_json(manifest_path)

    return {}


def find_successful_retries(old_job_id: str) -> list[dict]:
    retries: list[dict] = []

    if not GOLD_TRANSCRIPTS_DIR.exists():
        return retries

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

        retries.append(
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

    return sorted(retries, key=lambda x: x.get("mtime") or 0, reverse=True)


def mark_failed_as_retried(
    failed_job_dir: Path,
    old_job_id: str,
    retry_source: str,
) -> Path:
    successful_retries = find_successful_retries(old_job_id)

    if not successful_retries:
        raise RuntimeError(
            "Retry завершился с кодом 0, но успешный gold с "
            f"retry_of_job_id={old_job_id} не найден."
        )

    latest_retry = successful_retries[0]

    marker = {
        "status": "retried_successfully",
        "old_job_id": old_job_id,
        "retry_source": retry_source,
        "marked_at": now_iso(),
        "latest_retry": latest_retry,
        "all_successful_retries": successful_retries,
        "notes": (
            "Старый failed не удалён автоматически. "
            "Он сохранён как диагностический слепок исходной ошибки."
        ),
    }

    marker_path = failed_job_dir / "RETRIED_SUCCESSFULLY.json"

    with marker_path.open("w", encoding="utf-8") as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)

    return marker_path


def resolve_failed_dir(job_id: str | None, failed_dir: str | None) -> Path:
    if failed_dir:
        path = Path(failed_dir).resolve()
    elif job_id:
        path = FAILED_DIR / job_id
    else:
        raise RuntimeError("Нужно указать --job-id или --failed-dir")

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


def find_bronze_by_job_id(old_job_id: str) -> Path | None:
    patterns = [
        f"{old_job_id}.*",
        f"{old_job_id}__*",
    ]

    matches: list[Path] = []

    for pattern in patterns:
        matches.extend(BRONZE_DIR.glob(pattern))

    files = [p for p in matches if p.is_file()]

    if not files:
        return None

    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def resolve_bronze_source(context: dict, old_job_id: str) -> Path:
    paths = context.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}

    bronze_value = paths.get("bronze_raw_original")

    if bronze_value:
        bronze_path = Path(bronze_value)

        if bronze_path.exists():
            return bronze_path.resolve()

        print(f"WARNING: bronze_raw_original из job_context не найден: {bronze_path}")

    fallback = find_bronze_by_job_id(old_job_id)

    if fallback:
        print(f"Найден bronze fallback по old_job_id: {fallback}")
        return fallback.resolve()

    raise FileNotFoundError(
        "Не найден исходник в bronze для retry. "
        f"old_job_id={old_job_id}"
    )


def run_retry(
    bronze_path: Path,
    original_filename: str,
    old_job_id: str,
    keep_processing: bool,
) -> int:
    command = [
        sys.executable,
        str(PROCESS_ONE_SCRIPT),
        "--input",
        str(bronze_path),
        "--from-bronze",
        "--original-filename",
        original_filename,
        "--retry-of-job-id",
        old_job_id,
    ]

    if keep_processing:
        command.append("--keep-processing")

    print("")
    print("=== Retry failed job ===")
    print(f"old_job_id:        {old_job_id}")
    print(f"bronze source:     {bronze_path}")
    print(f"original filename: {original_filename}")
    print("")
    print("Команда:")
    print(" ".join(f'"{x}"' if " " in str(x) else str(x) for x in command))
    print("")

    result = subprocess.run(command, cwd=str(BASE_DIR))

    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Перезапуск failed job от исходника в bronze/raw_original."
    )

    parser.add_argument(
        "--job-id",
        type=str,
        default=None,
        help="job_id из data\\failed\\<job_id>",
    )

    parser.add_argument(
        "--failed-dir",
        type=str,
        default=None,
        help="Полный путь к папке failed job.",
    )

    parser.add_argument(
        "--keep-processing",
        action="store_true",
        help="Оставить processing после успешного retry для отладки.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    failed_job_dir = resolve_failed_dir(
        job_id=args.job_id,
        failed_dir=args.failed_dir,
    )

    context_path = find_context_path(failed_job_dir)
    context = read_json(context_path)

    old_job_id = context.get("job_id") or failed_job_dir.name
    original_filename = context.get("original_filename")

    if not original_filename:
        raise RuntimeError(
            f"В job_context нет original_filename: {context_path}"
        )

    bronze_path = resolve_bronze_source(
        context=context,
        old_job_id=old_job_id,
    )

    exit_code = run_retry(
        bronze_path=bronze_path,
        original_filename=original_filename,
        old_job_id=old_job_id,
        keep_processing=args.keep_processing,
    )

    if exit_code == 0:
        marker_path = mark_failed_as_retried(
            failed_job_dir=failed_job_dir,
            old_job_id=old_job_id,
            retry_source="bronze",
        )

        print("")
        print("=== Failed job marked as retried ===")
        print(f"marker: {marker_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())