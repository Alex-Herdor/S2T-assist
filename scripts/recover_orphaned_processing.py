from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_paths import (
    PROJECT_ROOT,
    BRONZE_RAW_DIR,
    PROCESSING_DIR,
    SILVER_ASR_JSON_DIR,
    GOLD_TRANSCRIPTS_DIR,
    FAILED_DIR,
)


CLEANUP_FAILED_FILENAME = "CLEANUP_FAILED.txt"

INTERNAL_FILENAMES = {
    ".gitkeep",
    ".gitignore",
}


@dataclass
class ProcessingJobReport:
    level: str
    job_id: str
    status: str
    message: str
    processing_dir: str
    age_hours: float
    current_step: str | None = None
    failed_step: str | None = None
    original_filename: str | None = None
    context_path: str | None = None
    bronze_path: str | None = None
    silver_json_path: str | None = None
    gold_result_dir: str | None = None
    failed_dir: str | None = None
    cleanup_failed: bool = False
    suggestion: str | None = None


def rel_path(path: Path | None) -> str | None:
    if path is None:
        return None

    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except Exception:
        return str(path)


def is_internal_file(path: Path) -> bool:
    return path.name.lower() in INTERNAL_FILENAMES


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError("JSON root is not object")

    return data


def safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        return read_json(path)
    except Exception:
        return None


def path_from_json_value(value: Any) -> Path | None:
    if not value:
        return None

    try:
        path = Path(str(value))
    except Exception:
        return None

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def first_existing_path(paths: list[Path | None]) -> Path | None:
    for path in paths:
        if path and path.exists():
            return path

    return None


def find_context_path(job_dir: Path) -> Path | None:
    candidates = [
        job_dir / "job_context.json",
        job_dir / "processing" / "job_context.json",
    ]

    return first_existing_path(candidates)


def find_bronze_path(job_id: str, context: dict[str, Any] | None) -> Path | None:
    paths = context.get("paths", {}) if context else {}

    if not isinstance(paths, dict):
        paths = {}

    from_context = path_from_json_value(paths.get("bronze_raw_original"))

    candidates: list[Path | None] = [
        from_context,
    ]

    candidates.extend(sorted(BRONZE_RAW_DIR.glob(f"{job_id}.*")))
    candidates.extend(sorted(BRONZE_RAW_DIR.glob(f"{job_id}__*")))

    return first_existing_path(candidates)


def find_silver_json_path(job_id: str, context: dict[str, Any] | None) -> Path | None:
    paths = context.get("paths", {}) if context else {}

    if not isinstance(paths, dict):
        paths = {}

    from_context = path_from_json_value(paths.get("silver_asr_json"))

    candidates = [
        from_context,
        SILVER_ASR_JSON_DIR / f"{job_id}.json",
    ]

    return first_existing_path(candidates)


def find_failed_dir(job_id: str, context: dict[str, Any] | None) -> Path | None:
    paths = context.get("paths", {}) if context else {}

    if not isinstance(paths, dict):
        paths = {}

    from_context = path_from_json_value(paths.get("failed_dir"))

    candidates = [
        from_context,
        FAILED_DIR / job_id,
    ]

    path = first_existing_path(candidates)

    if path and path.is_dir():
        return path

    return None


def find_gold_result_dir(job_id: str, context: dict[str, Any] | None) -> Path | None:
    paths = context.get("paths", {}) if context else {}

    if not isinstance(paths, dict):
        paths = {}

    direct_candidates = [
        path_from_json_value(paths.get("gold_result_dir")),
        path_from_json_value(paths.get("gold_transcript_dir")),
    ]

    direct = first_existing_path(direct_candidates)

    if direct and direct.is_dir():
        return direct

    if not GOLD_TRANSCRIPTS_DIR.exists():
        return None

    for result_dir in sorted(GOLD_TRANSCRIPTS_DIR.iterdir()):
        if not result_dir.is_dir():
            continue

        context_path = result_dir / "job_context.json"
        manifest_path = result_dir / "manifest.json"

        gold_context = safe_read_json(context_path)
        manifest = safe_read_json(manifest_path)

        context_job_id = gold_context.get("job_id") if gold_context else None
        manifest_job_id = manifest.get("job_id") if manifest else None

        if str(context_job_id) == job_id or str(manifest_job_id) == job_id:
            return result_dir

    return None


def get_job_age_hours(job_dir: Path) -> float:
    try:
        age_seconds = time.time() - job_dir.stat().st_mtime
    except Exception:
        age_seconds = 0

    return max(age_seconds / 3600, 0)


def get_original_filename(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None

    value = context.get("original_filename")

    if value:
        return str(value)

    paths = context.get("paths", {})

    if isinstance(paths, dict):
        input_path = path_from_json_value(paths.get("input"))

        if input_path:
            return input_path.name

    return None


def classify_processing_job(
    job_dir: Path,
    orphan_hours: float,
    include_recent: bool,
) -> ProcessingJobReport | None:
    job_id = job_dir.name
    age_hours = get_job_age_hours(job_dir)
    context_path = find_context_path(job_dir)
    context = safe_read_json(context_path) if context_path else None

    context_job_id = str(context.get("job_id")) if context and context.get("job_id") else job_id

    current_step = str(context.get("current_step")) if context and context.get("current_step") else None
    failed_step = str(context.get("failed_step")) if context and context.get("failed_step") else None
    original_filename = get_original_filename(context)

    cleanup_failed = (job_dir / CLEANUP_FAILED_FILENAME).exists()

    bronze_path = find_bronze_path(context_job_id, context)
    silver_json_path = find_silver_json_path(context_job_id, context)
    gold_result_dir = find_gold_result_dir(context_job_id, context)
    failed_dir = find_failed_dir(context_job_id, context)

    if age_hours < orphan_hours and not include_recent and not cleanup_failed:
        return None

    if age_hours < orphan_hours:
        return ProcessingJobReport(
            level="ok",
            job_id=context_job_id,
            status="recent_processing",
            message="Processing job ещё не старше orphan threshold.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            current_step=current_step,
            failed_step=failed_step,
            original_filename=original_filename,
            context_path=rel_path(context_path),
            bronze_path=rel_path(bronze_path),
            silver_json_path=rel_path(silver_json_path),
            gold_result_dir=rel_path(gold_result_dir),
            failed_dir=rel_path(failed_dir),
            cleanup_failed=cleanup_failed,
            suggestion="Обычно ничего делать не нужно. Проверь, не выполняется ли сейчас обработка.",
        )

    if not context_path:
        return ProcessingJobReport(
            level="warn",
            job_id=context_job_id,
            status="orphan_missing_context",
            message="Processing job старше threshold и не содержит job_context.json.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            cleanup_failed=cleanup_failed,
            suggestion="Проверить папку вручную. В будущем можно будет перенести такой snapshot в failed.",
        )

    if context_path and context is None:
        return ProcessingJobReport(
            level="warn",
            job_id=context_job_id,
            status="orphan_invalid_context",
            message="Processing job старше threshold, но job_context.json не читается.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            context_path=rel_path(context_path),
            cleanup_failed=cleanup_failed,
            suggestion="Проверить job_context.json вручную. В будущем можно будет перенести snapshot в failed.",
        )

    if gold_result_dir:
        return ProcessingJobReport(
            level="warn",
            job_id=context_job_id,
            status="orphan_gold_exists",
            message="Processing job старше threshold, но связанный gold-result уже существует.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            current_step=current_step,
            failed_step=failed_step,
            original_filename=original_filename,
            context_path=rel_path(context_path),
            bronze_path=rel_path(bronze_path),
            silver_json_path=rel_path(silver_json_path),
            gold_result_dir=rel_path(gold_result_dir),
            failed_dir=rel_path(failed_dir),
            cleanup_failed=cleanup_failed,
            suggestion="Проверить gold-result. Если он корректен, processing можно будет очистить будущим cleanup-инструментом.",
        )

    if silver_json_path:
        original_arg = original_filename or "<original_filename>"

        return ProcessingJobReport(
            level="warn",
            job_id=context_job_id,
            status="orphan_silver_without_gold",
            message="Processing job старше threshold, silver ASR JSON есть, но gold-result не найден.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            current_step=current_step,
            failed_step=failed_step,
            original_filename=original_filename,
            context_path=rel_path(context_path),
            bronze_path=rel_path(bronze_path),
            silver_json_path=rel_path(silver_json_path),
            gold_result_dir=rel_path(gold_result_dir),
            failed_dir=rel_path(failed_dir),
            cleanup_failed=cleanup_failed,
            suggestion=(
                "Кандидат на repair из silver без повторного WhisperX. "
                f"Вручную: python scripts\\repair_gold_from_json.py --json \"{rel_path(silver_json_path)}\" "
                f"--original-filename \"{original_arg}\""
            ),
        )

    if failed_dir:
        return ProcessingJobReport(
            level="warn",
            job_id=context_job_id,
            status="orphan_failed_exists",
            message="Processing job старше threshold, и связанный failed snapshot уже существует.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            current_step=current_step,
            failed_step=failed_step,
            original_filename=original_filename,
            context_path=rel_path(context_path),
            bronze_path=rel_path(bronze_path),
            silver_json_path=rel_path(silver_json_path),
            gold_result_dir=rel_path(gold_result_dir),
            failed_dir=rel_path(failed_dir),
            cleanup_failed=cleanup_failed,
            suggestion="Проверить failed snapshot. Если он корректен, processing можно будет очистить будущим cleanup-инструментом.",
        )

    if bronze_path:
        return ProcessingJobReport(
            level="warn",
            job_id=context_job_id,
            status="orphan_bronze_available",
            message="Processing job старше threshold, gold/silver/failed не найдены, но bronze original есть.",
            processing_dir=rel_path(job_dir) or str(job_dir),
            age_hours=age_hours,
            current_step=current_step,
            failed_step=failed_step,
            original_filename=original_filename,
            context_path=rel_path(context_path),
            bronze_path=rel_path(bronze_path),
            silver_json_path=rel_path(silver_json_path),
            gold_result_dir=rel_path(gold_result_dir),
            failed_dir=rel_path(failed_dir),
            cleanup_failed=cleanup_failed,
            suggestion="Кандидат на перенос в failed или полный retry от bronze. В первой версии скрипт только сообщает об этом.",
        )

    return ProcessingJobReport(
        level="error",
        job_id=context_job_id,
        status="orphan_no_recovery_source",
        message="Processing job старше threshold, но не найдено gold/silver/failed/bronze.",
        processing_dir=rel_path(job_dir) or str(job_dir),
        age_hours=age_hours,
        current_step=current_step,
        failed_step=failed_step,
        original_filename=original_filename,
        context_path=rel_path(context_path),
        bronze_path=rel_path(bronze_path),
        silver_json_path=rel_path(silver_json_path),
        gold_result_dir=rel_path(gold_result_dir),
        failed_dir=rel_path(failed_dir),
        cleanup_failed=cleanup_failed,
        suggestion="Проверить папку вручную. Автоматическое восстановление невозможно без источника.",
    )


def collect_reports(
    orphan_hours: float,
    include_recent: bool,
    job_id_filter: str | None,
) -> list[ProcessingJobReport]:
    reports: list[ProcessingJobReport] = []

    if not PROCESSING_DIR.exists():
        return reports

    for job_dir in sorted(PROCESSING_DIR.iterdir()):
        if not job_dir.is_dir():
            continue

        if is_internal_file(job_dir):
            continue

        if job_id_filter and job_dir.name != job_id_filter:
            context_path = find_context_path(job_dir)
            context = safe_read_json(context_path) if context_path else None
            context_job_id = str(context.get("job_id")) if context and context.get("job_id") else None

            if context_job_id != job_id_filter:
                continue

        report = classify_processing_job(
            job_dir=job_dir,
            orphan_hours=orphan_hours,
            include_recent=include_recent,
        )

        if report:
            reports.append(report)

    return reports


def report_to_dict(report: ProcessingJobReport) -> dict[str, Any]:
    return {
        "level": report.level,
        "job_id": report.job_id,
        "status": report.status,
        "message": report.message,
        "processing_dir": report.processing_dir,
        "age_hours": round(report.age_hours, 2),
        "current_step": report.current_step,
        "failed_step": report.failed_step,
        "original_filename": report.original_filename,
        "context_path": report.context_path,
        "bronze_path": report.bronze_path,
        "silver_json_path": report.silver_json_path,
        "gold_result_dir": report.gold_result_dir,
        "failed_dir": report.failed_dir,
        "cleanup_failed": report.cleanup_failed,
        "suggestion": report.suggestion,
    }


def summarize_reports(reports: list[ProcessingJobReport]) -> dict[str, int]:
    return {
        "ok": sum(1 for item in reports if item.level == "ok"),
        "warn": sum(1 for item in reports if item.level == "warn"),
        "error": sum(1 for item in reports if item.level == "error"),
        "total": len(reports),
    }


def status_counts(reports: list[ProcessingJobReport]) -> dict[str, int]:
    counts: dict[str, int] = {}

    for report in reports:
        counts[report.status] = counts.get(report.status, 0) + 1

    return dict(sorted(counts.items()))


def print_json_output(
    reports: list[ProcessingJobReport],
    orphan_hours: float,
    include_recent: bool,
    job_id_filter: str | None,
) -> None:
    payload = {
        "project_root": str(PROJECT_ROOT),
        "processing_dir": str(PROCESSING_DIR),
        "orphan_hours": orphan_hours,
        "include_recent": include_recent,
        "job_id_filter": job_id_filter,
        "summary": summarize_reports(reports),
        "status_counts": status_counts(reports),
        "reports": [report_to_dict(report) for report in reports],
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_text_output(
    reports: list[ProcessingJobReport],
    orphan_hours: float,
    include_recent: bool,
    job_id_filter: str | None,
    limit: int | None,
    verbose: bool,
) -> None:
    summary = summarize_reports(reports)
    counts = status_counts(reports)

    print("")
    print("=" * 100)
    print("ORPHANED PROCESSING RECOVERY CHECK")
    print("=" * 100)
    print(f"project_root:    {PROJECT_ROOT}")
    print(f"processing_dir:  {PROCESSING_DIR}")
    print(f"orphan_hours:    {orphan_hours}")
    print(f"include_recent:  {include_recent}")
    print(f"job_id_filter:   {job_id_filter or '-'}")
    print(
        f"summary:         ok={summary['ok']} "
        f"warn={summary['warn']} "
        f"error={summary['error']} "
        f"total={summary['total']}"
    )

    if counts:
        counts_text = " ".join(f"{key}={value}" for key, value in counts.items())
        print(f"status_counts:   {counts_text}")
    else:
        print("status_counts:   -")

    print("=" * 100)

    visible_reports = reports

    if limit is not None:
        visible_reports = visible_reports[:limit]

    if not visible_reports:
        print("[OK] Orphaned processing jobs не найдены.")
        print("")
        return

    for report in visible_reports:
        prefix = {
            "ok": "OK",
            "warn": "WARN",
            "error": "ERROR",
        }.get(report.level, report.level.upper())

        print(f"[{prefix}] {report.status}: {report.message}")
        print(f"       job_id:         {report.job_id}")
        print(f"       age_hours:      {report.age_hours:.2f}")
        print(f"       processing_dir: {report.processing_dir}")

        if verbose:
            print(f"       current_step:   {report.current_step or '-'}")
            print(f"       failed_step:    {report.failed_step or '-'}")
            print(f"       original_file:  {report.original_filename or '-'}")
            print(f"       context:        {report.context_path or '-'}")
            print(f"       bronze:         {report.bronze_path or '-'}")
            print(f"       silver_json:    {report.silver_json_path or '-'}")
            print(f"       gold:           {report.gold_result_dir or '-'}")
            print(f"       failed:         {report.failed_dir or '-'}")
            print(f"       cleanup_failed: {report.cleanup_failed}")

        if report.suggestion:
            print(f"       suggestion:     {report.suggestion}")

    print("")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only анализ зависших data/processing job после kill процесса, "
            "перезагрузки или нештатного падения пайплайна."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--orphan-hours",
        type=float,
        default=6.0,
        help="Через сколько часов processing job считать кандидатом в orphan.",
    )

    parser.add_argument(
        "--include-recent",
        action="store_true",
        help="Показывать также свежие processing job, которые ещё не старше orphan threshold.",
    )

    parser.add_argument(
        "--job-id",
        default=None,
        help="Проверить только один job_id.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести отчёт в JSON.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать подробные пути и поля job_context.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество выводимых reports в текстовом режиме.",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Возвращать ненулевой exit code при WARN, а не только при ERROR.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    reports = collect_reports(
        orphan_hours=args.orphan_hours,
        include_recent=args.include_recent,
        job_id_filter=args.job_id,
    )

    if args.json:
        print_json_output(
            reports=reports,
            orphan_hours=args.orphan_hours,
            include_recent=args.include_recent,
            job_id_filter=args.job_id,
        )
    else:
        print_text_output(
            reports=reports,
            orphan_hours=args.orphan_hours,
            include_recent=args.include_recent,
            job_id_filter=args.job_id,
            limit=args.limit,
            verbose=args.verbose,
        )

    summary = summarize_reports(reports)

    if summary["error"] > 0:
        return 2

    if args.strict and summary["warn"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())