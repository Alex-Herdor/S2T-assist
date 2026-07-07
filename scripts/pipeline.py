from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from datetime import datetime

IGNORED_LANDING_SUFFIXES = (
    ".uploading",
    ".tmp",
    ".part",
    ".crdownload",
    ".done",
)

IGNORED_LANDING_NAMES = {
    ".gitignore",
    ".gitkeep",
}

SUPPORTED_MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}

def is_supported_landing_file(path: Path) -> bool:
    name_lower = path.name.lower()

    if not path.is_file():
        return False

    if path.name in IGNORED_LANDING_NAMES:
        return False

    if path.name.startswith("."):
        return False

    if any(name_lower.endswith(suffix) for suffix in IGNORED_LANDING_SUFFIXES):
        return False

    return path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS


def is_file_stable(path: Path, stable_seconds: int = 60, probe_seconds: int = 5) -> tuple[bool, str]:
    try:
        first_stat = path.stat()
    except FileNotFoundError:
        return False, "file disappeared before stat"

    age_seconds = time.time() - first_stat.st_mtime
    if age_seconds < stable_seconds:
        return False, f"file is too new: age={age_seconds:.1f}s, required={stable_seconds}s"

    time.sleep(probe_seconds)

    try:
        second_stat = path.stat()
    except FileNotFoundError:
        return False, "file disappeared during stability probe"

    if first_stat.st_size != second_stat.st_size:
        return False, "file size changed during stability probe"

    if first_stat.st_mtime_ns != second_stat.st_mtime_ns:
        return False, "file mtime changed during stability probe"

    return True, "stable"


def find_next_ready_landing_file(landing_dir: Path) -> Path | None:
    if not landing_dir.exists():
        print(f"[process] landing dir does not exist: {landing_dir}")
        return None

    candidates = sorted(
        [path for path in landing_dir.iterdir() if is_supported_landing_file(path)],
        key=lambda path: path.stat().st_mtime,
    )

    if not candidates:
        print("[process] no files found in landing")
        return None

    for candidate in candidates:
        stable, reason = is_file_stable(candidate)
        if stable:
            print(f"[process] selected file: {candidate}")
            return candidate

        print(f"[process] skip not-ready file: {candidate.name}; reason={reason}")

    print("[process] no ready files found in landing")
    return None

from project_paths import (
    PROJECT_ROOT,
    ENV_PATH,
    WHISPERX_CONFIG_PATH,
    DATA_DIR,
    LANDING_DIR,
    BRONZE_RAW_DIR,
    PROCESSING_DIR,
    SILVER_AUDIO_FLAC_DIR,
    SILVER_ASR_JSON_DIR,
    GOLD_TRANSCRIPTS_DIR,
    FAILED_DIR,
    ARCHIVE_DIR,
    HF_CACHE_DIR,
    get_hf_token,
    load_dotenv_if_exists,
    resolve_project_path,
)


SCRIPT_DIR = Path(__file__).resolve().parent

PROCESS_ONE_SCRIPT = SCRIPT_DIR / "process_one_file.py"
PROCESS_LANDING_ONCE_SCRIPT = SCRIPT_DIR / "process_landing_once.py"
STATUS_JOBS_SCRIPT = SCRIPT_DIR / "status_jobs.py"
RETRY_FAILED_JOB_SCRIPT = SCRIPT_DIR / "retry_failed_job.py"
REPAIR_GOLD_FROM_JSON_SCRIPT = SCRIPT_DIR / "repair_gold_from_json.py"
CHECK_STORAGE_INTEGRITY_SCRIPT = SCRIPT_DIR / "check_storage_integrity.py"
REBUILD_JOBS_DB_SCRIPT = SCRIPT_DIR / "rebuild_jobs_db.py"
JOBS_DB_STATUS_SCRIPT = SCRIPT_DIR / "jobs_db_status.py"
JOBS_DB_PATH = DATA_DIR / "jobs.db"
RECOVER_ORPHANED_PROCESSING_SCRIPT = SCRIPT_DIR / "recover_orphaned_processing.py"
INIT_DIRS_SCRIPT = SCRIPT_DIR / "init_dirs.py"

IMPORTANT_SCRIPTS = [
    SCRIPT_DIR / "project_paths.py",
    SCRIPT_DIR / "run_whisperx.py",
    SCRIPT_DIR / "process_one_file.py",
    SCRIPT_DIR / "process_landing_once.py",
    SCRIPT_DIR / "status_jobs.py",
    SCRIPT_DIR / "retry_failed_job.py",
    SCRIPT_DIR / "repair_gold_from_json.py",
    SCRIPT_DIR / "check_storage_integrity.py",
    SCRIPT_DIR / "recover_orphaned_processing.py",
    SCRIPT_DIR / "init_dirs.py",
    SCRIPT_DIR / "pipeline.py",
    SCRIPT_DIR / "rebuild_jobs_db.py",
    SCRIPT_DIR / "jobs_db_status.py",
]


@dataclass
class CheckResult:
    level: str
    code: str
    message: str
    details: str | None = None


def now_none(value: Any) -> Any:
    return value


def command_to_text(command: list[str]) -> str:
    return subprocess.list2cmdline([str(x) for x in command])


def run_command(command: list[str], dry_run: bool = False) -> int:
    print("")
    print("=" * 100)
    print(command_to_text(command))
    print("=" * 100)
    print("")

    if dry_run:
        print("Dry-run: команда не запускалась.")
        return 0

    result = subprocess.run(
        [str(x) for x in command],
        cwd=str(PROJECT_ROOT),
    )

    return result.returncode


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return data

    raise RuntimeError(f"Ожидался JSON object: {path}")


def safe_read_json(path: Path) -> dict:
    try:
        return read_json(path)
    except Exception:
        return {}


def find_failed_context_path(failed_job_dir: Path) -> Path | None:
    candidates = [
        failed_job_dir / "processing" / "job_context.json",
        failed_job_dir / "job_context.json",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def resolve_failed_job_dir(job_id: str | None, failed_dir: str | None) -> Path:
    if failed_dir:
        path = Path(failed_dir).resolve()
    elif job_id:
        path = FAILED_DIR / job_id
    else:
        raise RuntimeError("Нужно указать --job-id или --failed-dir")

    if not path.exists():
        raise FileNotFoundError(f"Failed job не найден: {path}")

    if not path.is_dir():
        raise RuntimeError(f"Failed job path не является папкой: {path}")

    return path


def detect_old_job_id(failed_job_dir: Path, context: dict) -> str:
    return str(context.get("job_id") or failed_job_dir.name)


def silver_json_exists_for_failed(old_job_id: str, context: dict) -> bool:
    paths = context.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}

    silver_path_value = paths.get("silver_asr_json")

    if silver_path_value and Path(silver_path_value).exists():
        return True

    fallback = SILVER_ASR_JSON_DIR / f"{old_job_id}.json"

    return fallback.exists()


def bronze_exists_for_failed(old_job_id: str, context: dict) -> bool:
    paths = context.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}

    bronze_path_value = paths.get("bronze_raw_original")

    if bronze_path_value and Path(bronze_path_value).exists():
        return True

    patterns = [
        f"{old_job_id}.*",
        f"{old_job_id}__*",
    ]

    for pattern in patterns:
        if any(BRONZE_RAW_DIR.glob(pattern)):
            return True

    return False


def build_failed_ref_args(job_id: str | None, failed_dir: str | None) -> list[str]:
    if failed_dir:
        return ["--failed-dir", failed_dir]

    if job_id:
        return ["--job-id", job_id]

    raise RuntimeError("Нужно указать --job-id или --failed-dir")


def cmd_process(args: argparse.Namespace) -> int:
    if args.input:
        incompatible = []

        if args.dry_run:
            incompatible.append("--dry-run")
        if args.show_sizes:
            incompatible.append("--show-sizes")
        if args.limit is not None:
            incompatible.append("--limit")
        if args.newest_first:
            incompatible.append("--newest-first")
        if args.continue_on_error:
            incompatible.append("--continue-on-error")

        if incompatible:
            print(
                "ERROR: эти параметры используются только для обработки landing: "
                + ", ".join(incompatible)
            )
            return 2

        command = [
            sys.executable,
            str(PROCESS_ONE_SCRIPT),
            "--input",
            args.input,
        ]

        if args.keep_processing:
            command.append("--keep-processing")

        return run_command(command)

    if args.limit is not None and args.limit != 1:
        print("ERROR: process без --input теперь обрабатывает максимум один файл за запуск.")
        print("Используй --limit 1 или не указывай --limit.")
        return 2

    if args.continue_on_error:
        print("[process] --continue-on-error ignored: one-file mode processes at most one file.")

    input_file = find_next_ready_landing_file(LANDING_DIR)

    if input_file is None:
        return 0

    if args.show_sizes:
        try:
            size_mb = input_file.stat().st_size / 1024 / 1024
            print(f"[process] selected file size: {size_mb:.2f} MB")
        except FileNotFoundError:
            print("[process] selected file disappeared before processing")
            return 0

    command = [
        sys.executable,
        str(PROCESS_ONE_SCRIPT),
        "--input",
        str(input_file),
    ]

    if args.keep_processing:
        command.append("--keep-processing")

    if args.dry_run:
        print("[process] dry-run: selected file would be processed")
        print(f"[process] dry-run input: {input_file}")
        return 0

    return run_command(command)


def cmd_status(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(STATUS_JOBS_SCRIPT),
    ]

    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])

    if args.sizes:
        command.append("--sizes")

    if args.orphan_hours is not None:
        command.extend(["--orphan-hours", str(args.orphan_hours)])

    if args.json:
        command.append("--json")

    if args.landing:
        command.append("--landing-only")

    if args.processing:
        command.append("--processing-only")

    if args.failed:
        command.append("--failed-only")

    if args.gold:
        command.append("--gold-only")

    return run_command(command)


def cmd_repair(args: argparse.Namespace) -> int:
    failed_job_dir = resolve_failed_job_dir(
        job_id=args.job_id,
        failed_dir=args.failed_dir,
    )

    context_path = find_failed_context_path(failed_job_dir)

    if not context_path:
        print(f"ERROR: не найден job_context.json в failed job: {failed_job_dir}")
        return 2

    context = safe_read_json(context_path)
    old_job_id = detect_old_job_id(failed_job_dir, context)

    has_silver = silver_json_exists_for_failed(old_job_id, context)
    has_bronze = bronze_exists_for_failed(old_job_id, context)

    if args.mode == "silver":
        selected_mode = "silver"
    elif args.mode == "bronze":
        selected_mode = "bronze"
    else:
        if has_silver:
            selected_mode = "silver"
        elif has_bronze:
            selected_mode = "bronze"
        else:
            print("")
            print("ERROR: не найден источник для восстановления.")
            print(f"failed job: {failed_job_dir}")
            print(f"job_id:     {old_job_id}")
            print("silver:     not found")
            print("bronze:     not found")
            return 2

    print("")
    print("=== Repair decision ===")
    print(f"failed job:       {failed_job_dir}")
    print(f"job_id:           {old_job_id}")
    print(f"context:          {context_path}")
    print(f"silver available: {'yes' if has_silver else 'no'}")
    print(f"bronze available: {'yes' if has_bronze else 'no'}")
    print(f"selected mode:    {selected_mode}")

    ref_args = build_failed_ref_args(
        job_id=args.job_id,
        failed_dir=args.failed_dir,
    )

    if selected_mode == "silver":
        if not has_silver:
            print("")
            print("ERROR: выбран mode=silver, но silver/asr_json для job не найден.")
            return 2

        command = [
            sys.executable,
            str(REPAIR_GOLD_FROM_JSON_SCRIPT),
            *ref_args,
        ]

        if args.no_mark_retried:
            command.append("--no-mark-retried")

        return run_command(command, dry_run=args.dry_run)

    if selected_mode == "bronze":
        if not has_bronze:
            print("")
            print("ERROR: выбран mode=bronze, но bronze/raw_original для job не найден.")
            return 2

        command = [
            sys.executable,
            str(RETRY_FAILED_JOB_SCRIPT),
            *ref_args,
        ]

        if args.keep_processing:
            command.append("--keep-processing")

        return run_command(command, dry_run=args.dry_run)

    print(f"ERROR: неизвестный repair mode: {selected_mode}")
    return 2


def cmd_init(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(INIT_DIRS_SCRIPT),
    ]

    return run_command(command)


def cmd_jobs_rebuild(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(REBUILD_JOBS_DB_SCRIPT),
    ]

    if args.db_path:
        command.extend(["--db-path", args.db_path])

    if args.dry_run:
        command.append("--dry-run")

    if args.json_output:
        command.append("--json")

    return run_command(command)


def cmd_jobs_status(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(JOBS_DB_STATUS_SCRIPT),
    ]

    if args.db_path:
        command.extend(["--db-path", args.db_path])

    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])

    if args.status:
        command.extend(["--status", args.status])

    if args.job_id:
        command.extend(["--job-id", args.job_id])

    if args.search:
        command.extend(["--search", args.search])

    if args.details:
        command.append("--details")

    if args.json_output:
        command.append("--json")

    return run_command(command)


def add_result(
    results: list[CheckResult],
    level: str,
    code: str,
    message: str,
    details: str | None = None,
) -> None:
    results.append(
        CheckResult(
            level=level,
            code=code,
            message=message,
            details=details,
        )
    )


def check_path_exists(
    results: list[CheckResult],
    code: str,
    path: Path,
    required: bool = True,
) -> None:
    if path.exists():
        add_result(results, "ok", code, f"Найдено: {path}")
        return

    if required:
        add_result(results, "error", code, f"Не найдено: {path}")
    else:
        add_result(results, "warn", code, f"Не найдено: {path}")


def check_writable_dir(
    results: list[CheckResult],
    code: str,
    path: Path,
    required: bool = True,
) -> None:
    if not path.exists():
        if required:
            add_result(results, "error", code, f"Папка не найдена: {path}")
        else:
            add_result(results, "warn", code, f"Папка не найдена: {path}")
        return

    if not path.is_dir():
        add_result(results, "error", code, f"Это не папка: {path}")
        return

    test_file = path / ".pipeline_write_test.tmp"

    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        add_result(results, "ok", code, f"Папка доступна на запись: {path}")
    except Exception as exc:
        add_result(
            results,
            "error" if required else "warn",
            code,
            f"Нет записи в папку: {path}",
            details=str(exc),
        )


def check_command_exists(
    results: list[CheckResult],
    code: str,
    command_name: str,
    required: bool = True,
) -> None:
    found = shutil.which(command_name)

    if found:
        add_result(results, "ok", code, f"Команда найдена: {command_name}", found)
        return

    add_result(
        results,
        "error" if required else "warn",
        code,
        f"Команда не найдена: {command_name}",
    )


def check_py_compile(results: list[CheckResult], script_path: Path) -> None:
    code = f"py_compile:{script_path.name}"

    if not script_path.exists():
        add_result(results, "error", code, f"Скрипт не найден: {script_path}")
        return

    try:
        py_compile.compile(str(script_path), doraise=True)
        add_result(results, "ok", code, f"Синтаксис OK: {script_path.name}")
    except Exception as exc:
        add_result(
            results,
            "error",
            code,
            f"Ошибка синтаксиса: {script_path.name}",
            details=str(exc),
        )


def check_storage_integrity_summary(
    results: list[CheckResult],
    verbose: bool = False,
) -> None:
    try:
        from check_storage_integrity import (
            collect_all_issues,
            indexes_to_summary,
            summarize_issues,
        )
    except Exception as exc:
        add_result(
            results,
            "error",
            "storage_integrity:import",
            "Не удалось импортировать check_storage_integrity.py.",
            details=str(exc),
        )
        return

    try:
        issues, indexes = collect_all_issues(
            orphan_hours=6.0,
            verbose=False,
        )

        issue_summary = summarize_issues(issues)
        storage_summary = indexes_to_summary(indexes)

    except Exception as exc:
        add_result(
            results,
            "error",
            "storage_integrity:run",
            "Не удалось выполнить проверку файлового хранилища.",
            details=str(exc),
        )
        return

    details = (
        f"issues: ok={issue_summary['ok']} "
        f"warn={issue_summary['warn']} "
        f"error={issue_summary['error']}; "
        f"storage: gold={storage_summary['gold_jobs']} "
        f"failed={storage_summary['failed_jobs']} "
        f"retried_failed={storage_summary['retried_failed_jobs']} "
        f"processing={storage_summary['processing_jobs']} "
        f"silver_json={storage_summary['silver_json_jobs']} "
        f"bronze={storage_summary['bronze_original_jobs']}; "
        "details: python scripts\\check_storage_integrity.py"
    )

    if issue_summary["error"] > 0:
        add_result(
            results,
            "error",
            "storage_integrity",
            "В файловом хранилище найдены ERROR-нарушения контракта.",
            details=details,
        )
        return

    if issue_summary["warn"] > 0:
        add_result(
            results,
            "warn",
            "storage_integrity",
            "В файловом хранилище найдены WARN-предупреждения.",
            details=details,
        )
        return

    add_result(
        results,
        "ok",
        "storage_integrity",
        "Файловое хранилище выглядит целым.",
        details=details if verbose else None,
    )


def check_orphaned_processing_summary(
    results: list[CheckResult],
    verbose: bool = False,
) -> None:
    try:
        from recover_orphaned_processing import (
            collect_reports,
            status_counts,
            summarize_reports,
        )
    except Exception as exc:
        add_result(
            results,
            "error",
            "orphaned_processing:import",
            "Не удалось импортировать recover_orphaned_processing.py.",
            details=str(exc),
        )
        return

    try:
        reports = collect_reports(
            orphan_hours=6.0,
            include_recent=False,
            job_id_filter=None,
        )

        report_summary = summarize_reports(reports)
        counts = status_counts(reports)

    except Exception as exc:
        add_result(
            results,
            "error",
            "orphaned_processing:run",
            "Не удалось выполнить проверку зависших processing job.",
            details=str(exc),
        )
        return

    counts_text = " ".join(
        f"{key}={value}"
        for key, value in counts.items()
    ) or "-"

    details = (
        f"reports: ok={report_summary['ok']} "
        f"warn={report_summary['warn']} "
        f"error={report_summary['error']} "
        f"total={report_summary['total']}; "
        f"status_counts: {counts_text}; "
        "details: python scripts\\recover_orphaned_processing.py"
    )

    if report_summary["error"] > 0:
        add_result(
            results,
            "error",
            "orphaned_processing",
            "Найдены processing job без понятного источника восстановления.",
            details=details,
        )
        return

    if report_summary["warn"] > 0:
        add_result(
            results,
            "warn",
            "orphaned_processing",
            "Найдены зависшие или требующие внимания processing job.",
            details=details,
        )
        return

    add_result(
        results,
        "ok",
        "orphaned_processing",
        "Зависшие processing job не найдены.",
        details=details if verbose else None,
    )


def check_jobs_db(results: list[CheckResult]) -> None:
    if not JOBS_DB_PATH.exists():
        results.append(
            CheckResult(
                "warn",
                "jobs_db:missing",
                "jobs.db не найден.",
                "Это не ошибка для первого запуска. Создать индекс: python scripts\\pipeline.py jobs rebuild",
            )
        )
        return

    try:
        db_mtime = datetime.fromtimestamp(JOBS_DB_PATH.stat().st_mtime).isoformat(timespec="seconds")

        with sqlite3.connect(JOBS_DB_PATH) as connection:
            table_exists = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'jobs'
                """
            ).fetchone()

            if not table_exists:
                results.append(
                    CheckResult(
                        "error",
                        "jobs_db:missing_jobs_table",
                        "jobs.db найден, но таблица jobs отсутствует.",
                        f"Пересобери индекс: python scripts\\pipeline.py jobs rebuild; db={JOBS_DB_PATH}",
                    )
                )
                return

            jobs_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            status_rows = connection.execute(
                """
                SELECT status, COUNT(*)
                FROM jobs
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()

        statuses = ", ".join(f"{status or 'UNKNOWN'}={count}" for status, count in status_rows)

        results.append(
            CheckResult(
                "ok",
                "jobs_db",
                f"jobs.db доступен: jobs={jobs_count}.",
                f"updated_at={db_mtime}; statuses: {statuses or 'none'}",
            )
        )

    except Exception as exc:
        results.append(
            CheckResult(
                "ERROR",
                "jobs_db:open_failed",
                "Не удалось прочитать jobs.db.",
                f"{exc}; db={JOBS_DB_PATH}",
            )
        )


def collect_doctor_results(verbose: bool = False) -> list[CheckResult]:
    load_dotenv_if_exists()

    results: list[CheckResult] = []

    add_result(results, "ok", "project_root", f"PROJECT_ROOT: {PROJECT_ROOT}")

    required_dirs = [
        DATA_DIR,
        LANDING_DIR,
        BRONZE_RAW_DIR,
        PROCESSING_DIR,
        SILVER_AUDIO_FLAC_DIR,
        SILVER_ASR_JSON_DIR,
        GOLD_TRANSCRIPTS_DIR,
        FAILED_DIR,
        ARCHIVE_DIR,
        HF_CACHE_DIR,
    ]

    for path in required_dirs:
        check_path_exists(results, f"dir:{path.relative_to(PROJECT_ROOT)}", path)

    writable_dirs = [
        LANDING_DIR,
        BRONZE_RAW_DIR,
        PROCESSING_DIR,
        SILVER_AUDIO_FLAC_DIR,
        SILVER_ASR_JSON_DIR,
        GOLD_TRANSCRIPTS_DIR,
        FAILED_DIR,
        HF_CACHE_DIR,
    ]

    for path in writable_dirs:
        check_writable_dir(results, f"write:{path.relative_to(PROJECT_ROOT)}", path)

    check_path_exists(
        results,
        "config:whisperx_config",
        WHISPERX_CONFIG_PATH,
        required=True,
    )

    check_path_exists(
        results,
        "env:file",
        ENV_PATH,
        required=False,
    )

    config: dict = {}

    if WHISPERX_CONFIG_PATH.exists():
        try:
            config = read_json(WHISPERX_CONFIG_PATH)
            add_result(results, "ok", "config:json", "Конфиг читается как JSON.")
        except Exception as exc:
            add_result(
                results,
                "error",
                "config:json",
                "Конфиг не читается как JSON.",
                details=str(exc),
            )

    if config:
        hf_cache_dir = resolve_project_path(
            config.get("hf_cache_dir"),
            HF_CACHE_DIR,
        )

        add_result(
            results,
            "ok",
            "config:hf_cache_dir",
            f"HF cache dir: {hf_cache_dir}",
        )

        diarize = bool(config.get("diarize", False))
        token = get_hf_token()

        if diarize and not token:
            add_result(
                results,
                "error",
                "hf_token",
                "В конфиге включена diarization, но HF_TOKEN не найден.",
                "Задай HF_TOKEN в .env или отключи diarize в config/whisperx_config.json.",
            )
        elif diarize and token:
            add_result(
                results,
                "ok",
                "hf_token",
                "HF_TOKEN найден. Значение не выводится.",
            )
        else:
            add_result(
                results,
                "ok",
                "hf_token",
                "Diarization выключена, HF_TOKEN не обязателен.",
            )

    check_command_exists(results, "command:ffmpeg", "ffmpeg", required=True)
    check_command_exists(results, "command:whisperx", "whisperx", required=True)

    for script_path in IMPORTANT_SCRIPTS:
        check_py_compile(results, script_path)

    check_storage_integrity_summary(
        results=results,
        verbose=verbose,
    )

    check_jobs_db(results)

    check_orphaned_processing_summary(
        results=results,
        verbose=verbose,
    )

    return results


def print_doctor_results(
    results: list[CheckResult],
    json_output: bool = False,
    verbose: bool = False,
) -> None:
    if json_output:
        payload = {
            "project_root": str(PROJECT_ROOT),
            "summary": summarize_results(results),
            "checks": [
                {
                    "level": item.level,
                    "code": item.code,
                    "message": item.message,
                    "details": item.details,
                }
                for item in results
            ],
        }

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    summary = summarize_results(results)

    print("")
    print("=" * 100)
    print("PIPELINE DOCTOR")
    print("=" * 100)
    print(f"project_root: {PROJECT_ROOT}")
    print(
        f"summary: ok={summary['ok']} "
        f"warn={summary['warn']} "
        f"error={summary['error']}"
    )
    print("=" * 100)

    for item in results:
        if not verbose and item.level == "ok":
            continue

        prefix = {
            "ok": "OK",
            "warn": "WARN",
            "error": "ERROR",
        }.get(item.level, item.level.upper())

        print(f"[{prefix}] {item.code}: {item.message}")

        if item.details:
            print(f"       {item.details}")

    if not verbose and summary["error"] == 0 and summary["warn"] == 0:
        print("[OK] Критичных проблем не найдено.")

    print("")


def summarize_results(results: list[CheckResult]) -> dict[str, int]:
    return {
        "ok": sum(1 for item in results if item.level == "ok"),
        "warn": sum(1 for item in results if item.level == "warn"),
        "error": sum(1 for item in results if item.level == "error"),
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    results = collect_doctor_results(verbose=args.verbose)
    print_doctor_results(
        results,
        json_output=args.json,
        verbose=args.verbose,
    )

    summary = summarize_results(results)

    if summary["error"] > 0:
        return 2

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Единая точка входа для локального WhisperX meeting pipeline. "
            "Обычная эксплуатация: process, status, repair, doctor, init."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    process_parser = subparsers.add_parser(
        "process",
        help="Обработать один входной файл или один готовый файл из landing.",
        description=(
            "Обработка входных файлов. Если указан --input, обрабатывается конкретный файл. "
            "Если --input не указан, выбирается один готовый файл из data/landing."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    process_parser.add_argument(
        "--input",
        default=None,
        help=(
            "Путь к одному входному аудио/видео файлу. "
            "Обычно используется файл из data/landing. "
            "Если параметр не указан, будет обработан landing batch."
        ),
    )
    process_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, какие файлы были бы обработаны. Работает только для batch-режима без --input.",
    )
    process_parser.add_argument(
        "--show-sizes",
        action="store_true",
        help="Показать размеры файлов в списке batch-обработки.",
    )
    process_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Совместимость со старым batch-режимом. Сейчас допустимо только --limit 1.",
    )
    process_parser.add_argument(
        "--newest-first",
        action="store_true",
        help="Обрабатывать сначала самые новые файлы. По умолчанию сначала самые старые.",
    )
    process_parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Совместимость со старым batch-режимом. В one-file режиме игнорируется.",
    )
    process_parser.add_argument(
        "--keep-processing",
        action="store_true",
        help="Не удалять data/processing/<job_id> после success. Полезно для отладки.",
    )
    process_parser.set_defaults(func=cmd_process)

    status_parser = subparsers.add_parser(
        "status",
        help="Показать состояние локального хранилища.",
        description=(
            "Read-only аудит текущего состояния landing, processing, failed и gold. "
            "Команда ничего не меняет и ничего не удаляет."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    status_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Сколько элементов показывать в каждой секции.",
    )
    status_parser.add_argument(
        "--sizes",
        action="store_true",
        help="Посчитать размеры файлов/папок. Может быть медленнее на больших архивах.",
    )
    status_parser.add_argument(
        "--orphan-hours",
        type=int,
        default=None,
        help="Через сколько часов processing job считать кандидатом в orphan.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести полный отчёт в JSON для будущего UI/API или автоматизации.",
    )
    status_parser.add_argument(
        "--landing",
        "--landing-only",
        dest="landing",
        action="store_true",
        help="Показать только landing.",
    )
    status_parser.add_argument(
        "--processing",
        "--processing-only",
        dest="processing",
        action="store_true",
        help="Показать только processing.",
    )
    status_parser.add_argument(
        "--failed",
        "--failed-only",
        dest="failed",
        action="store_true",
        help="Показать только failed jobs.",
    )
    status_parser.add_argument(
        "--gold",
        "--gold-only",
        dest="gold",
        action="store_true",
        help="Показать только успешные gold results.",
    )
    status_parser.set_defaults(func=cmd_status)

    repair_parser = subparsers.add_parser(
        "repair",
        help="Автоматически восстановить failed job через silver JSON или bronze.",
        description=(
            "Умное восстановление failed job. В mode=auto сначала проверяется silver/asr_json. "
            "Если JSON уже есть, выполняется быстрый repair gold без повторного WhisperX. "
            "Если JSON нет, но есть bronze/raw_original, выполняется полный retry от bronze."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    repair_parser.add_argument(
        "--job-id",
        default=None,
        help="ID failed job из data/failed/<job_id>.",
    )
    repair_parser.add_argument(
        "--failed-dir",
        default=None,
        help="Прямой путь к папке failed job. Альтернатива --job-id.",
    )
    repair_parser.add_argument(
        "--mode",
        choices=["auto", "silver", "bronze"],
        default="auto",
        help=(
            "Стратегия восстановления. auto: сначала silver/asr_json, потом bronze. "
            "silver: принудительно repair из JSON. bronze: принудительно полный retry от исходника."
        ),
    )
    repair_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать выбранную стратегию и команду, но не запускать repair/retry.",
    )
    repair_parser.add_argument(
        "--keep-processing",
        action="store_true",
        help="Для bronze retry: сохранить processing после успешной обработки.",
    )
    repair_parser.add_argument(
        "--no-mark-retried",
        action="store_true",
        help="Для silver repair: не создавать RETRIED_SUCCESSFULLY.json в failed job.",
    )
    repair_parser.set_defaults(func=cmd_repair)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Проверить готовность локальной установки и базовые системные риски.",
        description=(
            "Диагностика окружения и структуры проекта: папки, доступ на запись, "
            "config, .env, ffmpeg, whisperx, HF token при включённой diarization, "
            "синтаксис основных Python-скриптов, краткая storage integrity summary "
            "и orphaned processing summary."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести результат диагностики в JSON.",
    )
    doctor_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать не только WARN/ERROR, но и успешные OK-проверки.",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    init_parser = subparsers.add_parser(
        "init",
        help="Создать локальную структуру папок.",
        description="Создаёт data/*, hf_cache и .gitkeep после clone или переноса проекта.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    init_parser.set_defaults(func=cmd_init)

    jobs_parser = subparsers.add_parser(
        "jobs",
        help="Работа с восстановимым jobs.db индексом.",
        description=(
            "Команды для пересборки и просмотра data/jobs.db. "
            "jobs.db является восстановимым индексом поверх файловой структуры, "
            "а не source of truth."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    jobs_subparsers = jobs_parser.add_subparsers(
        dest="jobs_command",
        required=True,
    )

    jobs_rebuild_parser = jobs_subparsers.add_parser(
        "rebuild",
        help="Пересобрать data/jobs.db из файловой структуры.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    jobs_rebuild_parser.add_argument("--db-path", default=None)
    jobs_rebuild_parser.add_argument("--dry-run", action="store_true")
    jobs_rebuild_parser.add_argument("--json", action="store_true", dest="json_output")
    jobs_rebuild_parser.set_defaults(func=cmd_jobs_rebuild)

    jobs_status_parser = jobs_subparsers.add_parser(
        "status",
        help="Показать jobs из data/jobs.db.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    jobs_status_parser.add_argument("--db-path", default=None)
    jobs_status_parser.add_argument("--limit", type=int, default=20)
    jobs_status_parser.add_argument("--status")
    jobs_status_parser.add_argument("--job-id")
    jobs_status_parser.add_argument("--search")
    jobs_status_parser.add_argument("--details", action="store_true")
    jobs_status_parser.add_argument("--json", action="store_true", dest="json_output")
    jobs_status_parser.set_defaults(func=cmd_jobs_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("")
        print("Остановлено пользователем.")
        return 130
    except Exception as exc:
        print("")
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())