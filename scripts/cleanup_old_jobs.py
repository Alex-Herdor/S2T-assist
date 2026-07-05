from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_paths import (
    PROJECT_ROOT,
    PROCESSING_DIR,
    FAILED_DIR,
)


LOCAL_BACKUPS_DIR = PROJECT_ROOT / ".local_backups"
LOGS_DIR = PROJECT_ROOT / "logs"

FAILED_RETRY_MARKER = "RETRIED_SUCCESSFULLY.json"
CLEANUP_FAILED_MARKER = "CLEANUP_FAILED.txt"

SKIP_WALK_DIR_NAMES = {
    ".git",
    ".local_backups",
    "data",
    "hf_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
}

INTERNAL_FILENAMES = {
    ".gitkeep",
    ".gitignore",
}


@dataclass
class CleanupCandidate:
    level: str
    category: str
    reason: str
    path: str
    size_bytes: int
    age_hours: float | None = None
    suggested_action: str | None = None
    notes: str | None = None


@dataclass
class DeleteResult:
    status: str
    category: str
    path: str
    size_bytes: int
    message: str


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except Exception:
        return str(path)


def now_ts() -> float:
    return time.time()


def age_hours(path: Path) -> float | None:
    try:
        return max((now_ts() - path.stat().st_mtime) / 3600, 0)
    except Exception:
        return None


def is_internal_file(path: Path) -> bool:
    return path.name.lower() in INTERNAL_FILENAMES


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def dir_size(path: Path) -> int:
    total = 0

    if not path.exists():
        return 0

    for root, dirs, files in os.walk(path):
        dirs[:] = [
            dirname
            for dirname in dirs
            if dirname not in {".git"}
        ]

        root_path = Path(root)

        for filename in files:
            file_path = root_path / filename

            try:
                total += file_path.stat().st_size
            except Exception:
                pass

    return total


def format_size(size_bytes: int) -> str:
    value = float(size_bytes)

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{size_bytes} B"


def add_candidate(
    candidates: list[CleanupCandidate],
    level: str,
    category: str,
    reason: str,
    path: Path,
    size_bytes: int,
    age_hours_value: float | None = None,
    suggested_action: str | None = None,
    notes: str | None = None,
) -> None:
    candidates.append(
        CleanupCandidate(
            level=level,
            category=category,
            reason=reason,
            path=rel_path(path),
            size_bytes=size_bytes,
            age_hours=age_hours_value,
            suggested_action=suggested_action,
            notes=notes,
        )
    )


def iter_project_files_and_dirs() -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    dirs_found: list[Path] = []

    for root, dirs, filenames in os.walk(PROJECT_ROOT):
        root_path = Path(root)

        dirs[:] = [
            dirname
            for dirname in dirs
            if dirname not in SKIP_WALK_DIR_NAMES
        ]

        for dirname in dirs:
            dirs_found.append(root_path / dirname)

        for filename in filenames:
            path = root_path / filename

            if is_internal_file(path):
                continue

            files.append(path)

    return files, dirs_found


def collect_pycache_candidates(candidates: list[CleanupCandidate]) -> None:
    _, dirs_found = iter_project_files_and_dirs()

    for path in dirs_found:
        if path.name != "__pycache__":
            continue

        add_candidate(
            candidates=candidates,
            level="safe",
            category="pycache",
            reason="Python bytecode cache можно безопасно удалить.",
            path=path,
            size_bytes=dir_size(path),
            age_hours_value=age_hours(path),
            suggested_action="Будущая команда: python scripts\\cleanup_old_jobs.py --delete-pycache",
        )


def is_backup_file(path: Path) -> bool:
    name = path.name.lower()

    if name.endswith(".bak"):
        return True

    if ".bak_" in name:
        return True

    if name.endswith(".backup"):
        return True

    if name.endswith(".old"):
        return True

    if name.endswith(".temp_fail_test"):
        return True

    return False


def collect_backup_file_candidates(
    candidates: list[CleanupCandidate],
    bak_days: float,
) -> None:
    files, _ = iter_project_files_and_dirs()
    threshold_hours = bak_days * 24

    for path in files:
        if not is_backup_file(path):
            continue

        item_age = age_hours(path)

        if item_age is None:
            continue

        if item_age < threshold_hours:
            continue

        add_candidate(
            candidates=candidates,
            level="safe",
            category="backup_files",
            reason=f"Локальный backup/old-файл старше {bak_days} дней.",
            path=path,
            size_bytes=file_size(path),
            age_hours_value=item_age,
            suggested_action=f"Будущая команда: python scripts\\cleanup_old_jobs.py --delete-backup-files-older-than-days {bak_days:g}",
        )


def collect_local_backup_candidates(
    candidates: list[CleanupCandidate],
    local_backup_days: float,
    include_young: bool,
) -> None:
    if not LOCAL_BACKUPS_DIR.exists():
        return

    threshold_hours = local_backup_days * 24

    for path in sorted(LOCAL_BACKUPS_DIR.iterdir()):
        if not path.is_dir():
            continue

        item_age = age_hours(path)

        if item_age is None:
            continue

        if item_age < threshold_hours and not include_young:
            continue

        level = "safe" if item_age >= threshold_hours else "info"

        reason = (
            f"Локальный backup старше {local_backup_days} дней."
            if item_age >= threshold_hours
            else "Локальный backup найден, но он младше cleanup threshold."
        )

        add_candidate(
            candidates=candidates,
            level=level,
            category="local_backups",
            reason=reason,
            path=path,
            size_bytes=dir_size(path),
            age_hours_value=item_age,
            suggested_action=f"Будущая команда: python scripts\\cleanup_old_jobs.py --delete-local-backups-older-than-days {local_backup_days:g}",
        )


def collect_log_candidates(
    candidates: list[CleanupCandidate],
    log_days: float,
) -> None:
    if not LOGS_DIR.exists():
        return

    threshold_hours = log_days * 24

    for path in sorted(LOGS_DIR.rglob("*")):
        if not path.is_file():
            continue

        item_age = age_hours(path)

        if item_age is None:
            continue

        if item_age < threshold_hours:
            continue

        add_candidate(
            candidates=candidates,
            level="safe",
            category="logs",
            reason=f"Log-файл старше {log_days} дней.",
            path=path,
            size_bytes=file_size(path),
            age_hours_value=item_age,
            suggested_action=f"Будущая команда: python scripts\\cleanup_old_jobs.py --delete-logs-older-than-days {log_days:g}",
        )


def collect_retried_failed_candidates(
    candidates: list[CleanupCandidate],
    failed_days: float,
) -> None:
    if not FAILED_DIR.exists():
        return

    threshold_hours = failed_days * 24

    for failed_dir in sorted(FAILED_DIR.iterdir()):
        if not failed_dir.is_dir():
            continue

        marker_path = failed_dir / FAILED_RETRY_MARKER

        if not marker_path.exists():
            continue

        item_age = age_hours(failed_dir)

        if item_age is None:
            continue

        if item_age < threshold_hours:
            continue

        add_candidate(
            candidates=candidates,
            level="caution",
            category="retried_failed",
            reason=(
                "Failed job помечен как успешно восстановленный. "
                "Можно рассматривать для архивирования/очистки после ручной проверки."
            ),
            path=failed_dir,
            size_bytes=dir_size(failed_dir),
            age_hours_value=item_age,
            suggested_action=(
                "Будущая команда будет добавлена отдельно. "
                "Пока рекомендуется только ручная проверка."
            ),
            notes="Не удалять автоматически в первой версии.",
        )


def collect_processing_candidates(
    candidates: list[CleanupCandidate],
    processing_days: float,
) -> None:
    if not PROCESSING_DIR.exists():
        return

    threshold_hours = processing_days * 24

    for job_dir in sorted(PROCESSING_DIR.iterdir()):
        if not job_dir.is_dir():
            continue

        item_age = age_hours(job_dir)

        if item_age is None:
            continue

        cleanup_marker = job_dir / CLEANUP_FAILED_MARKER

        if cleanup_marker.exists():
            add_candidate(
                candidates=candidates,
                level="caution",
                category="processing_cleanup_failed",
                reason="Processing job содержит CLEANUP_FAILED.txt.",
                path=job_dir,
                size_bytes=dir_size(job_dir),
                age_hours_value=item_age,
                suggested_action=(
                    "Сначала проверить gold/silver/failed через doctor и "
                    "recover_orphaned_processing.py."
                ),
                notes="Не удалять автоматически в первой версии.",
            )
            continue

        if item_age >= threshold_hours:
            add_candidate(
                candidates=candidates,
                level="caution",
                category="old_processing",
                reason=f"Processing job старше {processing_days} дней.",
                path=job_dir,
                size_bytes=dir_size(job_dir),
                age_hours_value=item_age,
                suggested_action=(
                    "Сначала проверить через: python scripts\\recover_orphaned_processing.py --job-id "
                    f"{job_dir.name} --verbose"
                ),
                notes="Не удалять автоматически в первой версии.",
            )


def collect_candidates(
    local_backup_days: float,
    bak_days: float,
    log_days: float,
    failed_days: float,
    processing_days: float,
    include_young_backups: bool,
) -> list[CleanupCandidate]:
    candidates: list[CleanupCandidate] = []

    collect_pycache_candidates(candidates)
    collect_backup_file_candidates(candidates, bak_days=bak_days)
    collect_local_backup_candidates(
        candidates,
        local_backup_days=local_backup_days,
        include_young=include_young_backups,
    )
    collect_log_candidates(candidates, log_days=log_days)
    collect_retried_failed_candidates(candidates, failed_days=failed_days)
    collect_processing_candidates(candidates, processing_days=processing_days)

    candidates.sort(
        key=lambda item: (
            {"caution": 0, "safe": 1, "info": 2}.get(item.level, 9),
            item.category,
            item.path,
        )
    )

    return candidates


def candidate_to_dict(candidate: CleanupCandidate) -> dict[str, Any]:
    return {
        "level": candidate.level,
        "category": candidate.category,
        "reason": candidate.reason,
        "path": candidate.path,
        "size_bytes": candidate.size_bytes,
        "size_human": format_size(candidate.size_bytes),
        "age_hours": round(candidate.age_hours, 2) if candidate.age_hours is not None else None,
        "age_days": round(candidate.age_hours / 24, 2) if candidate.age_hours is not None else None,
        "suggested_action": candidate.suggested_action,
        "notes": candidate.notes,
    }


def summarize(candidates: list[CleanupCandidate]) -> dict[str, Any]:
    by_level: dict[str, int] = {}
    by_category: dict[str, int] = {}
    size_by_level: dict[str, int] = {}
    size_by_category: dict[str, int] = {}

    total_size = 0

    for item in candidates:
        by_level[item.level] = by_level.get(item.level, 0) + 1
        by_category[item.category] = by_category.get(item.category, 0) + 1

        size_by_level[item.level] = size_by_level.get(item.level, 0) + item.size_bytes
        size_by_category[item.category] = size_by_category.get(item.category, 0) + item.size_bytes

        total_size += item.size_bytes

    return {
        "total": len(candidates),
        "by_level": dict(sorted(by_level.items())),
        "by_category": dict(sorted(by_category.items())),
        "total_size_bytes": total_size,
        "total_size_human": format_size(total_size),
        "size_by_level": {
            key: {
                "bytes": value,
                "human": format_size(value),
            }
            for key, value in sorted(size_by_level.items())
        },
        "size_by_category": {
            key: {
                "bytes": value,
                "human": format_size(value),
            }
            for key, value in sorted(size_by_category.items())
        },
    }


def attention_summary(candidates: list[CleanupCandidate]) -> dict[str, int]:
    important_categories = {
        "retried_failed",
        "old_processing",
        "processing_cleanup_failed",
    }

    result: dict[str, int] = {}

    for item in candidates:
        if item.category in important_categories:
            result[item.category] = result.get(item.category, 0) + 1

    return dict(sorted(result.items()))
    
    
def delete_allowed_categories(args: argparse.Namespace) -> set[str]:
    allowed: set[str] = set()

    if args.delete_pycache:
        allowed.add("pycache")

    if args.delete_backup_files_older_than_days is not None:
        allowed.add("backup_files")

    if args.delete_local_backups_older_than_days is not None:
        allowed.add("local_backups")

    if args.delete_logs_older_than_days is not None:
        allowed.add("logs")

    return allowed


def has_delete_request(args: argparse.Namespace) -> bool:
    return bool(delete_allowed_categories(args))


def is_safe_delete_candidate(candidate: CleanupCandidate, allowed_categories: set[str]) -> bool:
    if candidate.level != "safe":
        return False

    if candidate.category not in allowed_categories:
        return False

    return True


def resolve_candidate_path(candidate: CleanupCandidate) -> Path:
    path = Path(candidate.path)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def is_path_inside_project(path: Path) -> bool:
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
        return True
    except Exception:
        return False


def delete_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
        return

    if path.is_file():
        path.unlink()
        return

    raise RuntimeError("Path is neither file nor directory")


def execute_deletions(
    candidates: list[CleanupCandidate],
    args: argparse.Namespace,
) -> list[DeleteResult]:
    allowed_categories = delete_allowed_categories(args)
    results: list[DeleteResult] = []

    if not allowed_categories:
        return results

    for candidate in candidates:
        if not is_safe_delete_candidate(candidate, allowed_categories):
            continue

        path = resolve_candidate_path(candidate)

        if not is_path_inside_project(path):
            results.append(
                DeleteResult(
                    status="skipped",
                    category=candidate.category,
                    path=candidate.path,
                    size_bytes=candidate.size_bytes,
                    message="Path is outside PROJECT_ROOT.",
                )
            )
            continue

        if not path.exists():
            results.append(
                DeleteResult(
                    status="skipped",
                    category=candidate.category,
                    path=candidate.path,
                    size_bytes=candidate.size_bytes,
                    message="Path does not exist.",
                )
            )
            continue

        if not args.yes:
            results.append(
                DeleteResult(
                    status="planned",
                    category=candidate.category,
                    path=candidate.path,
                    size_bytes=candidate.size_bytes,
                    message="Deletion requested, but --yes was not provided.",
                )
            )
            continue

        try:
            delete_path(path)
            results.append(
                DeleteResult(
                    status="deleted",
                    category=candidate.category,
                    path=candidate.path,
                    size_bytes=candidate.size_bytes,
                    message="Deleted.",
                )
            )
        except Exception as exc:
            results.append(
                DeleteResult(
                    status="error",
                    category=candidate.category,
                    path=candidate.path,
                    size_bytes=candidate.size_bytes,
                    message=str(exc),
                )
            )

    return results


def summarize_delete_results(results: list[DeleteResult]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    size_by_status: dict[str, int] = {}

    total_size = 0

    for item in results:
        by_status[item.status] = by_status.get(item.status, 0) + 1
        by_category[item.category] = by_category.get(item.category, 0) + 1
        size_by_status[item.status] = size_by_status.get(item.status, 0) + item.size_bytes
        total_size += item.size_bytes

    return {
        "total": len(results),
        "by_status": dict(sorted(by_status.items())),
        "by_category": dict(sorted(by_category.items())),
        "total_size_bytes": total_size,
        "total_size_human": format_size(total_size),
        "size_by_status": {
            key: {
                "bytes": value,
                "human": format_size(value),
            }
            for key, value in sorted(size_by_status.items())
        },
    }


def delete_result_to_dict(result: DeleteResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "category": result.category,
        "path": result.path,
        "size_bytes": result.size_bytes,
        "size_human": format_size(result.size_bytes),
        "message": result.message,
    }


def print_json_output(
    candidates: list[CleanupCandidate],
    args: argparse.Namespace,
    delete_results: list[DeleteResult],
) -> None:
    payload = {
        "mode": "delete" if has_delete_request(args) else "diagnostic_only",
        "delete_requested": has_delete_request(args),
        "delete_confirmed": bool(args.yes),
        "project_root": str(PROJECT_ROOT),
        "thresholds": {
            "local_backup_days": args.local_backup_days,
            "bak_days": args.bak_days,
            "log_days": args.log_days,
            "failed_days": args.failed_days,
            "processing_days": args.processing_days,
        },
        "summary": summarize(candidates),
        "attention": attention_summary(candidates),
        "delete_summary": summarize_delete_results(delete_results),
        "candidates": [candidate_to_dict(item) for item in candidates],
        "delete_results": [delete_result_to_dict(item) for item in delete_results],
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_text_output(
    candidates: list[CleanupCandidate],
    args: argparse.Namespace,
    delete_results: list[DeleteResult],
) -> None:
    summary = summarize(candidates)
    attention = attention_summary(candidates)
    delete_summary = summarize_delete_results(delete_results)

    mode = "delete" if has_delete_request(args) else "diagnostic_only"

    print("")
    print("=" * 100)
    print("CLEANUP OLD JOBS")
    print("=" * 100)
    print(f"project_root:       {PROJECT_ROOT}")
    print(f"mode:               {mode}")
    print(f"delete_confirmed:   {bool(args.yes)}")
    print(
        "thresholds:         "
        f"local_backups>{args.local_backup_days:g}d "
        f"bak>{args.bak_days:g}d "
        f"logs>{args.log_days:g}d "
        f"retried_failed>{args.failed_days:g}d "
        f"processing>{args.processing_days:g}d"
    )
    print(
        f"summary:            total={summary['total']} "
        f"size={summary['total_size_human']}"
    )
    print(f"by_level:           {summary['by_level'] or '-'}")
    print(f"by_category:        {summary['by_category'] or '-'}")

    if attention:
        attention_text = " ".join(
            f"{key}={value}"
            for key, value in attention.items()
        )
        print(f"attention:          {attention_text}")
        print("attention_note:     CAUTION-категории только подсвечиваются и не удаляются этим инструментом.")
    else:
        print("attention:          -")

    if has_delete_request(args):
        print(
            f"delete_summary:     total={delete_summary['total']} "
            f"size={delete_summary['total_size_human']} "
            f"by_status={delete_summary['by_status'] or '-'}"
        )

        if not args.yes:
            print("delete_note:        Удаление НЕ выполнено, потому что не указан --yes.")

    print("=" * 100)

    if not candidates:
        print("[OK] Кандидатов на очистку не найдено.")
        print("")
        return

    visible = candidates

    if args.level:
        visible = [
            item
            for item in visible
            if item.level == args.level
        ]

    if args.category:
        visible = [
            item
            for item in visible
            if item.category == args.category
        ]

    if args.limit is not None:
        visible = visible[: args.limit]

    if not visible:
        print("[OK] Нет кандидатов после применения фильтров.")
        print("")
    else:
        for item in visible:
            prefix = {
                "safe": "SAFE",
                "caution": "CAUTION",
                "info": "INFO",
            }.get(item.level, item.level.upper())

            print(f"[{prefix}] {item.category}: {item.reason}")
            print(f"       path: {item.path}")
            print(f"       size: {format_size(item.size_bytes)}")

            if item.age_hours is not None:
                print(f"       age:  {item.age_hours / 24:.2f} days")

            if args.verbose:
                if item.suggested_action:
                    print(f"       suggested_action: {item.suggested_action}")

                if item.notes:
                    print(f"       notes: {item.notes}")

    if delete_results:
        print("")
        print("-" * 100)
        print("DELETE RESULTS")
        print("-" * 100)

        for item in delete_results:
            prefix = {
                "planned": "PLANNED",
                "deleted": "DELETED",
                "skipped": "SKIPPED",
                "error": "ERROR",
            }.get(item.status, item.status.upper())

            print(f"[{prefix}] {item.category}: {item.path}")
            print(f"       size:    {format_size(item.size_bytes)}")
            print(f"       message: {item.message}")

    print("")
    print("CAUTION-категории retried_failed / old_processing / processing_cleanup_failed сейчас не удаляются.")
    print("Для SAFE-удаления нужен явный --delete-* флаг и --yes.")
    print("")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic-only анализ кандидатов на очистку в локальном WhisperX pipeline. "
            "Первая версия ничего не удаляет."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести результат в JSON.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показать suggested_action и notes.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество выводимых кандидатов.",
    )

    parser.add_argument(
        "--level",
        choices=["safe", "caution", "info"],
        default=None,
        help="Показать только кандидатов указанного уровня.",
    )

    parser.add_argument(
        "--category",
        default=None,
        help="Показать только одну категорию, например pycache или local_backups.",
    )

    parser.add_argument(
        "--local-backup-days",
        type=float,
        default=14.0,
        help="Возраст локальных .local_backups для попадания в кандидаты.",
    )

    parser.add_argument(
        "--include-young-backups",
        action="store_true",
        help="Показывать также свежие .local_backups как info.",
    )

    parser.add_argument(
        "--bak-days",
        type=float,
        default=0.0,
        help="Возраст .bak/.old/.backup файлов для попадания в кандидаты.",
    )

    parser.add_argument(
        "--log-days",
        type=float,
        default=14.0,
        help="Возраст logs/* файлов для попадания в кандидаты.",
    )

    parser.add_argument(
        "--failed-days",
        type=float,
        default=30.0,
        help="Возраст retried failed job для попадания в caution-кандидаты.",
    )

    parser.add_argument(
        "--processing-days",
        type=float,
        default=7.0,
        help="Возраст processing job для попадания в caution-кандидаты.",
    )
    
    parser.add_argument(
        "--delete-pycache",
        action="store_true",
        help="Удалить SAFE-кандидаты категории pycache. Требует --yes.",
    )

    parser.add_argument(
        "--delete-backup-files-older-than-days",
        type=float,
        default=None,
        help="Удалить SAFE .bak/.old/.backup файлы старше N дней. Требует --yes.",
    )

    parser.add_argument(
        "--delete-local-backups-older-than-days",
        type=float,
        default=None,
        help="Удалить SAFE .local_backups старше N дней. Требует --yes.",
    )

    parser.add_argument(
        "--delete-logs-older-than-days",
        type=float,
        default=None,
        help="Удалить SAFE logs/* файлы старше N дней. Требует --yes.",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтверждение удаления для SAFE-кандидатов. Без --yes удаление не выполняется.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    local_backup_days = (
        args.delete_local_backups_older_than_days
        if args.delete_local_backups_older_than_days is not None
        else args.local_backup_days
    )

    bak_days = (
        args.delete_backup_files_older_than_days
        if args.delete_backup_files_older_than_days is not None
        else args.bak_days
    )

    log_days = (
        args.delete_logs_older_than_days
        if args.delete_logs_older_than_days is not None
        else args.log_days
    )

    candidates = collect_candidates(
        local_backup_days=local_backup_days,
        bak_days=bak_days,
        log_days=log_days,
        failed_days=args.failed_days,
        processing_days=args.processing_days,
        include_young_backups=args.include_young_backups,
    )

    delete_results = execute_deletions(
        candidates=candidates,
        args=args,
    )

    if args.json:
        print_json_output(
            candidates=candidates,
            args=args,
            delete_results=delete_results,
        )
    else:
        print_text_output(
            candidates=candidates,
            args=args,
            delete_results=delete_results,
        )

    if any(item.status == "error" for item in delete_results):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())