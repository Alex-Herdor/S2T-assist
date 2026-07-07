from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_paths import (
    DATA_DIR,
    BRONZE_RAW_DIR,
    PROCESSING_DIR,
    SILVER_ASR_JSON_DIR,
    GOLD_TRANSCRIPTS_DIR,
    FAILED_DIR,
)


DB_PATH = DATA_DIR / "jobs.db"


@dataclass
class JobRecord:
    job_id: str
    original_filename: str | None = None
    status: str | None = None
    current_step: str | None = None
    source_mode: str | None = None
    attempt_type: str | None = None
    retry_of_job_id: str | None = None
    bronze_path: str | None = None
    silver_json_path: str | None = None
    gold_result_dir: str | None = None
    failed_dir: str | None = None
    processing_dir: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    error_message: str | None = None
    discovered_from: str | None = None


STATUS_PRIORITY = {
    "BRONZE_ONLY": 10,
    "ASR_JSON_ONLY": 20,
    "PROCESSING": 30,
    "FAILED": 40,
    "RETRIED_SUCCESSFULLY": 45,
    "SUCCESS": 50,
}


def utc_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def get_paths(context: dict[str, Any]) -> dict[str, Any]:
    paths = context.get("paths")
    return paths if isinstance(paths, dict) else {}


def merge_record(records: dict[str, JobRecord], incoming: JobRecord) -> None:
    existing = records.get(incoming.job_id)

    if existing is None:
        records[incoming.job_id] = incoming
        return

    existing_priority = STATUS_PRIORITY.get(existing.status or "", 0)
    incoming_priority = STATUS_PRIORITY.get(incoming.status or "", 0)

    primary, secondary = (incoming, existing) if incoming_priority >= existing_priority else (existing, incoming)

    merged = JobRecord(
        job_id=primary.job_id,
        original_filename=first_text(primary.original_filename, secondary.original_filename),
        status=first_text(primary.status, secondary.status),
        current_step=first_text(primary.current_step, secondary.current_step),
        source_mode=first_text(primary.source_mode, secondary.source_mode),
        attempt_type=first_text(primary.attempt_type, secondary.attempt_type),
        retry_of_job_id=first_text(primary.retry_of_job_id, secondary.retry_of_job_id),
        bronze_path=first_text(primary.bronze_path, secondary.bronze_path),
        silver_json_path=first_text(primary.silver_json_path, secondary.silver_json_path),
        gold_result_dir=first_text(primary.gold_result_dir, secondary.gold_result_dir),
        failed_dir=first_text(primary.failed_dir, secondary.failed_dir),
        processing_dir=first_text(primary.processing_dir, secondary.processing_dir),
        created_at=first_text(primary.created_at, secondary.created_at),
        updated_at=first_text(primary.updated_at, secondary.updated_at),
        error_message=first_text(primary.error_message, secondary.error_message),
        discovered_from=", ".join(
            sorted(
                {
                    item.strip()
                    for item in [
                        *(primary.discovered_from or "").split(","),
                        *(secondary.discovered_from or "").split(","),
                    ]
                    if item.strip()
                }
            )
        ),
    )

    records[incoming.job_id] = merged


def record_from_context(
    job_id: str,
    context: dict[str, Any],
    status: str,
    discovered_from: str,
    base_path: Path,
) -> JobRecord:
    paths = get_paths(context)

    return JobRecord(
        job_id=job_id,
        original_filename=first_text(
            context.get("original_filename"),
            context.get("input_filename"),
            context.get("source_filename"),
        ),
        status=status,
        current_step=first_text(context.get("current_step"), context.get("failed_step")),
        source_mode=first_text(context.get("source_mode")),
        attempt_type=first_text(context.get("attempt_type")),
        retry_of_job_id=first_text(context.get("retry_of_job_id")),
        bronze_path=first_text(paths.get("bronze_raw_original"), paths.get("bronze_path")),
        silver_json_path=first_text(paths.get("silver_asr_json"), paths.get("asr_json_path")),
        gold_result_dir=first_text(paths.get("gold_result_dir")),
        failed_dir=str(base_path) if status in {"FAILED", "RETRIED_SUCCESSFULLY"} else None,
        processing_dir=str(base_path) if status == "PROCESSING" else None,
        created_at=first_text(context.get("created_at"), context.get("started_at"), utc_from_mtime(base_path)),
        updated_at=first_text(context.get("updated_at"), context.get("finished_at"), utc_from_mtime(base_path)),
        error_message=first_text(context.get("error_message"), context.get("error")),
        discovered_from=discovered_from,
    )


def discover_gold(records: dict[str, JobRecord]) -> None:
    if not GOLD_TRANSCRIPTS_DIR.exists():
        return

    for result_dir in GOLD_TRANSCRIPTS_DIR.iterdir():
        if not result_dir.is_dir():
            continue

        context = read_json(result_dir / "job_context.json")
        manifest = read_json(result_dir / "manifest.json")
        job_id = first_text(context.get("job_id"), manifest.get("job_id"), result_dir.name)

        record = record_from_context(
            job_id=job_id,
            context=context,
            status="SUCCESS",
            discovered_from="gold",
            base_path=result_dir,
        )
        record.gold_result_dir = str(result_dir)
        record.updated_at = first_text(record.updated_at, utc_from_mtime(result_dir))

        merge_record(records, record)


def discover_failed(records: dict[str, JobRecord]) -> None:
    if not FAILED_DIR.exists():
        return

    for failed_dir in FAILED_DIR.iterdir():
        if not failed_dir.is_dir():
            continue

        context = read_json(failed_dir / "processing" / "job_context.json")
        if not context:
            context = read_json(failed_dir / "job_context.json")

        marker = failed_dir / "RETRIED_SUCCESSFULLY.json"
        status = "RETRIED_SUCCESSFULLY" if marker.exists() else "FAILED"
        job_id = first_text(context.get("job_id"), failed_dir.name)

        record = record_from_context(
            job_id=job_id,
            context=context,
            status=status,
            discovered_from="failed",
            base_path=failed_dir,
        )
        record.failed_dir = str(failed_dir)
        merge_record(records, record)


def discover_processing(records: dict[str, JobRecord]) -> None:
    if not PROCESSING_DIR.exists():
        return

    for processing_dir in PROCESSING_DIR.iterdir():
        if not processing_dir.is_dir():
            continue

        context = read_json(processing_dir / "job_context.json")
        job_id = first_text(context.get("job_id"), processing_dir.name)

        record = record_from_context(
            job_id=job_id,
            context=context,
            status="PROCESSING",
            discovered_from="processing",
            base_path=processing_dir,
        )
        record.processing_dir = str(processing_dir)
        merge_record(records, record)


def discover_silver(records: dict[str, JobRecord]) -> None:
    if not SILVER_ASR_JSON_DIR.exists():
        return

    for json_path in SILVER_ASR_JSON_DIR.glob("*.json"):
        job_id = json_path.stem

        record = JobRecord(
            job_id=job_id,
            status="ASR_JSON_ONLY",
            silver_json_path=str(json_path),
            created_at=utc_from_mtime(json_path),
            updated_at=utc_from_mtime(json_path),
            discovered_from="silver_asr_json",
        )
        merge_record(records, record)


def discover_bronze(records: dict[str, JobRecord]) -> None:
    if not BRONZE_RAW_DIR.exists():
        return

    for bronze_path in BRONZE_RAW_DIR.iterdir():
        if not bronze_path.is_file():
            continue
        if bronze_path.name in {".gitkeep", ".gitignore"}:
            continue

        job_id = bronze_path.stem.split("__", 1)[0]

        record = JobRecord(
            job_id=job_id,
            original_filename=bronze_path.name,
            status="BRONZE_ONLY",
            bronze_path=str(bronze_path),
            created_at=utc_from_mtime(bronze_path),
            updated_at=utc_from_mtime(bronze_path),
            discovered_from="bronze",
        )
        merge_record(records, record)


def collect_jobs() -> list[JobRecord]:
    records: dict[str, JobRecord] = {}

    discover_bronze(records)
    discover_silver(records)
    discover_processing(records)
    discover_failed(records)
    discover_gold(records)

    return sorted(records.values(), key=lambda job: job.updated_at or "", reverse=True)


def init_db(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS jobs")
    connection.execute(
        """
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            original_filename TEXT,
            status TEXT,
            current_step TEXT,
            source_mode TEXT,
            attempt_type TEXT,
            retry_of_job_id TEXT,
            bronze_path TEXT,
            silver_json_path TEXT,
            gold_result_dir TEXT,
            failed_dir TEXT,
            processing_dir TEXT,
            created_at TEXT,
            updated_at TEXT,
            error_message TEXT,
            discovered_from TEXT
        )
        """
    )
    connection.execute("CREATE INDEX idx_jobs_status ON jobs(status)")
    connection.execute("CREATE INDEX idx_jobs_updated_at ON jobs(updated_at)")
    connection.execute("CREATE INDEX idx_jobs_retry_of_job_id ON jobs(retry_of_job_id)")


def write_db(jobs: list[JobRecord], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        connection.executemany(
            """
            INSERT INTO jobs (
                job_id,
                original_filename,
                status,
                current_step,
                source_mode,
                attempt_type,
                retry_of_job_id,
                bronze_path,
                silver_json_path,
                gold_result_dir,
                failed_dir,
                processing_dir,
                created_at,
                updated_at,
                error_message,
                discovered_from
            )
            VALUES (
                :job_id,
                :original_filename,
                :status,
                :current_step,
                :source_mode,
                :attempt_type,
                :retry_of_job_id,
                :bronze_path,
                :silver_json_path,
                :gold_result_dir,
                :failed_dir,
                :processing_dir,
                :created_at,
                :updated_at,
                :error_message,
                :discovered_from
            )
            """,
            [asdict(job) for job in jobs],
        )


def print_human_summary(jobs: list[JobRecord], db_path: Path, dry_run: bool) -> None:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status or "UNKNOWN"] = counts.get(job.status or "UNKNOWN", 0) + 1

    print(f"jobs discovered: {len(jobs)}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    if dry_run:
        print(f"dry-run: database was not written: {db_path}")
    else:
        print(f"database written: {db_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild data/jobs.db from filesystem metadata.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    jobs = collect_jobs()

    if not args.dry_run:
        write_db(jobs, args.db_path)

    payload = {
        "db_path": str(args.db_path),
        "dry_run": args.dry_run,
        "jobs_count": len(jobs),
        "jobs": [asdict(job) for job in jobs],
    }

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human_summary(jobs, args.db_path, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())