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


REQUIRED_GOLD_FILES = [
    "whisperx_raw.json",
    "manifest.json",
    "job_context.json",
]

FAILED_MARKER_FILENAME = "RETRIED_SUCCESSFULLY.json"
CLEANUP_FAILED_FILENAME = "CLEANUP_FAILED.txt"

INTERNAL_FILENAMES = {
    ".gitkeep",
    ".gitignore",
}


@dataclass
class IntegrityIssue:
    level: str
    code: str
    message: str
    path: str | None = None
    details: str | None = None
    suggestion: str | None = None


@dataclass
class Indexes:
    gold_job_ids: set[str]
    failed_job_ids: set[str]
    retried_failed_job_ids: set[str]
    processing_job_ids: set[str]
    silver_job_ids: set[str]
    bronze_job_ids: set[str]


def rel_path(path: Path | None) -> str | None:
    if path is None:
        return None

    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except Exception:
        return str(path)


def is_internal_file(path: Path) -> bool:
    return path.name.lower() in INTERNAL_FILENAMES


def add_issue(
    issues: list[IntegrityIssue],
    level: str,
    code: str,
    message: str,
    path: Path | None = None,
    details: str | None = None,
    suggestion: str | None = None,
) -> None:
    issues.append(
        IntegrityIssue(
            level=level,
            code=code,
            message=message,
            path=rel_path(path),
            details=details,
            suggestion=suggestion,
        )
    )


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError("JSON root is not object")

    return data


def safe_read_json(
    issues: list[IntegrityIssue],
    path: Path,
    code: str,
    required: bool = True,
) -> dict[str, Any] | None:
    if not path.exists():
        add_issue(
            issues,
            "error" if required else "warn",
            code,
            "JSON-файл не найден.",
            path,
        )
        return None

    try:
        return read_json(path)
    except Exception as exc:
        add_issue(
            issues,
            "error" if required else "warn",
            code,
            "JSON-файл не читается.",
            path,
            details=str(exc),
        )
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


def first_existing_path(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path

    return None


def find_failed_context_path(failed_dir: Path) -> Path | None:
    return first_existing_path(
        [
            failed_dir / "processing" / "job_context.json",
            failed_dir / "job_context.json",
        ]
    )


def infer_job_id_from_bronze_filename(path: Path) -> str:
    name = path.name

    if "__" in name:
        return name.split("__", 1)[0]

    return path.stem


def collect_gold(
    issues: list[IntegrityIssue],
    verbose: bool,
) -> set[str]:
    gold_job_ids: set[str] = set()

    if not GOLD_TRANSCRIPTS_DIR.exists():
        add_issue(
            issues,
            "error",
            "gold_root_missing",
            "Папка gold/transcripts не найдена.",
            GOLD_TRANSCRIPTS_DIR,
        )
        return gold_job_ids

    for result_dir in sorted(GOLD_TRANSCRIPTS_DIR.iterdir()):
        if not result_dir.is_dir():
            continue

        required_paths = {
            filename: result_dir / filename
            for filename in REQUIRED_GOLD_FILES
        }

        missing_files = [
            filename
            for filename, path in required_paths.items()
            if not path.exists()
        ]

        if missing_files:
            add_issue(
                issues,
                "error",
                "gold_missing_required_files",
                "Gold-result неполный: отсутствуют обязательные файлы.",
                result_dir,
                details=", ".join(missing_files),
                suggestion="Проверить результат. Если есть silver/asr_json, выполнить repair из silver.",
            )

        manifest_path = required_paths["manifest.json"]
        context_path = required_paths["job_context.json"]
        raw_json_path = required_paths["whisperx_raw.json"]

        manifest = (
            safe_read_json(
                issues,
                manifest_path,
                "gold_manifest_invalid",
                required=True,
            )
            if manifest_path.exists()
            else None
        )

        context = (
            safe_read_json(
                issues,
                context_path,
                "gold_context_invalid",
                required=True,
            )
            if context_path.exists()
            else None
        )

        raw_json = (
            safe_read_json(
                issues,
                raw_json_path,
                "gold_raw_json_invalid",
                required=True,
            )
            if raw_json_path.exists()
            else None
        )

        job_id = None

        if context:
            job_id = context.get("job_id")

        if not job_id and manifest:
            job_id = manifest.get("job_id")

        if job_id:
            gold_job_ids.add(str(job_id))
        else:
            add_issue(
                issues,
                "warn",
                "gold_job_id_missing",
                "В gold-result не удалось определить job_id.",
                result_dir,
                suggestion="Проверить manifest.json и job_context.json.",
            )

        if raw_json is not None:
            segments = raw_json.get("segments")

            if not isinstance(segments, list):
                add_issue(
                    issues,
                    "warn",
                    "gold_raw_segments_missing",
                    "В whisperx_raw.json нет списка segments.",
                    raw_json_path,
                    suggestion="Проверить, корректно ли WhisperX сохранил результат.",
                )
            elif verbose:
                add_issue(
                    issues,
                    "ok",
                    "gold_raw_segments_ok",
                    f"Gold raw JSON содержит segments: {len(segments)}.",
                    raw_json_path,
                )

        if verbose and not missing_files and job_id:
            add_issue(
                issues,
                "ok",
                "gold_result_ok",
                f"Gold-result выглядит целым. job_id={job_id}",
                result_dir,
            )

    return gold_job_ids


def collect_silver(
    issues: list[IntegrityIssue],
    gold_job_ids: set[str],
    failed_job_ids: set[str],
    verbose: bool,
) -> set[str]:
    silver_job_ids: set[str] = set()

    if not SILVER_ASR_JSON_DIR.exists():
        add_issue(
            issues,
            "error",
            "silver_asr_json_root_missing",
            "Папка silver/asr_json не найдена.",
            SILVER_ASR_JSON_DIR,
        )
        return silver_job_ids

    for json_path in sorted(SILVER_ASR_JSON_DIR.glob("*.json")):
        job_id = json_path.stem
        silver_job_ids.add(job_id)

        data = safe_read_json(
            issues,
            json_path,
            "silver_json_invalid",
            required=True,
        )

        if data is not None:
            segments = data.get("segments")

            if not isinstance(segments, list):
                add_issue(
                    issues,
                    "warn",
                    "silver_segments_missing",
                    "В silver ASR JSON нет списка segments.",
                    json_path,
                )
            elif verbose:
                add_issue(
                    issues,
                    "ok",
                    "silver_json_ok",
                    f"Silver ASR JSON читается, segments: {len(segments)}.",
                    json_path,
                )

        if job_id not in gold_job_ids:
            if job_id in failed_job_ids:
                add_issue(
                    issues,
                    "warn",
                    "silver_without_gold_failed",
                    "Есть silver ASR JSON, но нет gold-result. Job есть в failed.",
                    json_path,
                    suggestion=f"Можно выполнить: python scripts\\pipeline.py repair --job-id {job_id} --mode silver",
                )
            else:
                add_issue(
                    issues,
                    "warn",
                    "silver_without_gold",
                    "Есть silver ASR JSON, но не найден связанный gold-result.",
                    json_path,
                    suggestion="Проверить, не оборвалась ли обработка после WhisperX. Возможно нужен repair из silver.",
                )

    return silver_job_ids


def collect_failed(
    issues: list[IntegrityIssue],
    verbose: bool,
) -> tuple[set[str], set[str]]:
    failed_job_ids: set[str] = set()
    retried_failed_job_ids: set[str] = set()

    if not FAILED_DIR.exists():
        add_issue(
            issues,
            "error",
            "failed_root_missing",
            "Папка failed не найдена.",
            FAILED_DIR,
        )
        return failed_job_ids, retried_failed_job_ids

    for failed_dir in sorted(FAILED_DIR.iterdir()):
        if not failed_dir.is_dir():
            continue

        job_id = failed_dir.name
        failed_job_ids.add(job_id)

        context_path = find_failed_context_path(failed_dir)

        if not context_path:
            add_issue(
                issues,
                "error",
                "failed_context_missing",
                "Failed job не содержит job_context.json.",
                failed_dir,
                suggestion="Проверить failed snapshot вручную.",
            )
        else:
            context = safe_read_json(
                issues,
                context_path,
                "failed_context_invalid",
                required=True,
            )

            if context:
                context_job_id = context.get("job_id")

                if context_job_id and str(context_job_id) != job_id:
                    add_issue(
                        issues,
                        "warn",
                        "failed_job_id_mismatch",
                        "job_id в failed path и job_context.json отличаются.",
                        context_path,
                        details=f"folder={job_id}, context={context_job_id}",
                    )

                if verbose:
                    current_step = context.get("current_step")
                    failed_step = context.get("failed_step")
                    add_issue(
                        issues,
                        "ok",
                        "failed_context_ok",
                        f"Failed context найден. current_step={current_step}, failed_step={failed_step}",
                        context_path,
                    )

        marker_path = failed_dir / FAILED_MARKER_FILENAME

        if marker_path.exists():
            retried_failed_job_ids.add(job_id)

            marker = safe_read_json(
                issues,
                marker_path,
                "failed_retry_marker_invalid",
                required=True,
            )

            if marker:
                status = marker.get("status")
                retry_source = marker.get("retry_source")

                if status != "retried_successfully":
                    add_issue(
                        issues,
                        "warn",
                        "failed_retry_marker_status_unexpected",
                        "Retry marker найден, но status отличается от retried_successfully.",
                        marker_path,
                        details=f"status={status}",
                    )

                if verbose:
                    add_issue(
                        issues,
                        "ok",
                        "failed_retry_marker_ok",
                        f"Retry marker найден. retry_source={retry_source}",
                        marker_path,
                    )

        if (failed_dir / "processing" / CLEANUP_FAILED_FILENAME).exists():
            add_issue(
                issues,
                "warn",
                "failed_cleanup_failed_marker",
                "В failed job найден CLEANUP_FAILED.txt.",
                failed_dir / "processing" / CLEANUP_FAILED_FILENAME,
            )

    return failed_job_ids, retried_failed_job_ids


def collect_processing(
    issues: list[IntegrityIssue],
    orphan_hours: float,
    verbose: bool,
) -> set[str]:
    processing_job_ids: set[str] = set()

    if not PROCESSING_DIR.exists():
        add_issue(
            issues,
            "error",
            "processing_root_missing",
            "Папка processing не найдена.",
            PROCESSING_DIR,
        )
        return processing_job_ids

    now_ts = time.time()
    orphan_seconds = orphan_hours * 3600

    for job_dir in sorted(PROCESSING_DIR.iterdir()):
        if not job_dir.is_dir():
            continue

        job_id = job_dir.name
        processing_job_ids.add(job_id)

        context_path = job_dir / "job_context.json"

        if not context_path.exists():
            add_issue(
                issues,
                "warn",
                "processing_context_missing",
                "Processing job не содержит job_context.json.",
                job_dir,
                suggestion="Если job давно не выполняется, рассмотреть перенос в failed или ручную очистку.",
            )
        else:
            context = safe_read_json(
                issues,
                context_path,
                "processing_context_invalid",
                required=False,
            )

            if context and verbose:
                current_step = context.get("current_step")
                add_issue(
                    issues,
                    "ok",
                    "processing_context_ok",
                    f"Processing context найден. current_step={current_step}",
                    context_path,
                )

        cleanup_marker = job_dir / CLEANUP_FAILED_FILENAME

        if cleanup_marker.exists():
            add_issue(
                issues,
                "warn",
                "processing_cleanup_failed_marker",
                "В processing job найден CLEANUP_FAILED.txt.",
                cleanup_marker,
                suggestion="Проверить, почему не удалилась processing-папка после success.",
            )

        try:
            age_seconds = now_ts - job_dir.stat().st_mtime
        except Exception:
            age_seconds = 0

        if age_seconds >= orphan_seconds:
            age_hours = age_seconds / 3600

            add_issue(
                issues,
                "warn",
                "processing_orphan_candidate",
                f"Processing job висит дольше порога orphan-hours: ~{age_hours:.1f} ч.",
                job_dir,
                suggestion="Проверить, не был ли процесс прерван. Позже это будет обрабатываться recover_orphaned_processing.py.",
            )
        elif verbose:
            age_hours = age_seconds / 3600
            add_issue(
                issues,
                "ok",
                "processing_recent",
                f"Processing job не выглядит orphan. age=~{age_hours:.1f} ч.",
                job_dir,
            )

    return processing_job_ids


def collect_bronze(
    issues: list[IntegrityIssue],
    known_job_ids: set[str],
    verbose: bool,
) -> set[str]:
    bronze_job_ids: set[str] = set()

    if not BRONZE_RAW_DIR.exists():
        add_issue(
            issues,
            "error",
            "bronze_root_missing",
            "Папка bronze/raw_original не найдена.",
            BRONZE_RAW_DIR,
        )
        return bronze_job_ids

    for path in sorted(BRONZE_RAW_DIR.iterdir()):
        if not path.is_file():
            continue

        if is_internal_file(path):
            continue

        job_id = infer_job_id_from_bronze_filename(path)
        bronze_job_ids.add(job_id)

        if job_id not in known_job_ids:
            add_issue(
                issues,
                "warn",
                "bronze_unreferenced",
                "Bronze original не связан с gold/failed/processing/silver по job_id.",
                path,
                suggestion="Проверить, не остался ли исходник от оборванной обработки.",
            )
        elif verbose:
            add_issue(
                issues,
                "ok",
                "bronze_referenced",
                f"Bronze original связан с известным job_id={job_id}.",
                path,
            )

    return bronze_job_ids


def collect_all_issues(
    orphan_hours: float,
    verbose: bool,
) -> tuple[list[IntegrityIssue], Indexes]:
    issues: list[IntegrityIssue] = []

    failed_job_ids, retried_failed_job_ids = collect_failed(
        issues=issues,
        verbose=verbose,
    )

    gold_job_ids = collect_gold(
        issues=issues,
        verbose=verbose,
    )

    processing_job_ids = collect_processing(
        issues=issues,
        orphan_hours=orphan_hours,
        verbose=verbose,
    )

    silver_job_ids = collect_silver(
        issues=issues,
        gold_job_ids=gold_job_ids,
        failed_job_ids=failed_job_ids,
        verbose=verbose,
    )

    known_before_bronze = (
        gold_job_ids
        | failed_job_ids
        | processing_job_ids
        | silver_job_ids
    )

    bronze_job_ids = collect_bronze(
        issues=issues,
        known_job_ids=known_before_bronze,
        verbose=verbose,
    )

    indexes = Indexes(
        gold_job_ids=gold_job_ids,
        failed_job_ids=failed_job_ids,
        retried_failed_job_ids=retried_failed_job_ids,
        processing_job_ids=processing_job_ids,
        silver_job_ids=silver_job_ids,
        bronze_job_ids=bronze_job_ids,
    )

    return issues, indexes


def summarize_issues(issues: list[IntegrityIssue]) -> dict[str, int]:
    return {
        "ok": sum(1 for issue in issues if issue.level == "ok"),
        "warn": sum(1 for issue in issues if issue.level == "warn"),
        "error": sum(1 for issue in issues if issue.level == "error"),
    }


def indexes_to_summary(indexes: Indexes) -> dict[str, int]:
    return {
        "gold_jobs": len(indexes.gold_job_ids),
        "failed_jobs": len(indexes.failed_job_ids),
        "retried_failed_jobs": len(indexes.retried_failed_job_ids),
        "processing_jobs": len(indexes.processing_job_ids),
        "silver_json_jobs": len(indexes.silver_job_ids),
        "bronze_original_jobs": len(indexes.bronze_job_ids),
    }


def issue_to_dict(issue: IntegrityIssue) -> dict[str, Any]:
    return {
        "level": issue.level,
        "code": issue.code,
        "message": issue.message,
        "path": issue.path,
        "details": issue.details,
        "suggestion": issue.suggestion,
    }


def print_json_output(
    issues: list[IntegrityIssue],
    indexes: Indexes,
    orphan_hours: float,
) -> None:
    payload = {
        "project_root": str(PROJECT_ROOT),
        "orphan_hours": orphan_hours,
        "summary": summarize_issues(issues),
        "storage_summary": indexes_to_summary(indexes),
        "issues": [issue_to_dict(issue) for issue in issues],
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_text_output(
    issues: list[IntegrityIssue],
    indexes: Indexes,
    orphan_hours: float,
    verbose: bool,
    limit: int | None,
) -> None:
    summary = summarize_issues(issues)
    storage_summary = indexes_to_summary(indexes)

    print("")
    print("=" * 100)
    print("STORAGE INTEGRITY CHECK")
    print("=" * 100)
    print(f"project_root:  {PROJECT_ROOT}")
    print(f"orphan_hours:  {orphan_hours}")
    print(
        f"summary:       ok={summary['ok']} "
        f"warn={summary['warn']} "
        f"error={summary['error']}"
    )
    print("-" * 100)
    print(
        "storage:       "
        f"gold={storage_summary['gold_jobs']} "
        f"failed={storage_summary['failed_jobs']} "
        f"retried_failed={storage_summary['retried_failed_jobs']} "
        f"processing={storage_summary['processing_jobs']} "
        f"silver_json={storage_summary['silver_json_jobs']} "
        f"bronze={storage_summary['bronze_original_jobs']}"
    )
    print("=" * 100)

    visible_issues = [
        issue
        for issue in issues
        if verbose or issue.level != "ok"
    ]

    if limit is not None:
        visible_issues = visible_issues[:limit]

    if not visible_issues:
        print("[OK] Критичных нарушений файлового контракта не найдено.")
        print("")
        return

    for issue in visible_issues:
        prefix = {
            "ok": "OK",
            "warn": "WARN",
            "error": "ERROR",
        }.get(issue.level, issue.level.upper())

        print(f"[{prefix}] {issue.code}: {issue.message}")

        if issue.path:
            print(f"       path: {issue.path}")

        if issue.details:
            print(f"       details: {issue.details}")

        if issue.suggestion:
            print(f"       suggestion: {issue.suggestion}")

    print("")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only проверка целостности файлового хранилища локального WhisperX pipeline."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести полный отчёт в JSON.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать OK-проверки, а не только WARN/ERROR.",
    )

    parser.add_argument(
        "--orphan-hours",
        type=float,
        default=6.0,
        help="Через сколько часов processing job считать кандидатом в orphan.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество выводимых issues в текстовом режиме.",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Возвращать ненулевой exit code не только при ERROR, но и при WARN.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    issues, indexes = collect_all_issues(
        orphan_hours=args.orphan_hours,
        verbose=args.verbose,
    )

    if args.json:
        print_json_output(
            issues=issues,
            indexes=indexes,
            orphan_hours=args.orphan_hours,
        )
    else:
        print_text_output(
            issues=issues,
            indexes=indexes,
            orphan_hours=args.orphan_hours,
            verbose=args.verbose,
            limit=args.limit,
        )

    summary = summarize_issues(issues)

    if summary["error"] > 0:
        return 2

    if args.strict and summary["warn"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())