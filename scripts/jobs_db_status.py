from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from project_paths import DATA_DIR


DEFAULT_DB_PATH = DATA_DIR / "jobs.db"


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"jobs.db не найден: {db_path}. Сначала запусти: python scripts\\rebuild_jobs_db.py"
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def build_query(args: argparse.Namespace) -> tuple[str, list[Any]]:
    where = []
    params: list[Any] = []

    if args.status:
        where.append("status = ?")
        params.append(args.status)

    if args.job_id:
        where.append("job_id = ?")
        params.append(args.job_id)

    if args.search:
        where.append(
            "("
            "job_id LIKE ? OR "
            "original_filename LIKE ? OR "
            "error_message LIKE ? OR "
            "discovered_from LIKE ?"
            ")"
        )
        value = f"%{args.search}%"
        params.extend([value, value, value, value])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    query = f"""
        SELECT
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
        FROM jobs
        {where_sql}
        ORDER BY COALESCE(updated_at, created_at, job_id) DESC
        LIMIT ?
    """
    params.append(args.limit)

    return query, params


def fetch_jobs(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    query, params = build_query(args)
    rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def fetch_counts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM jobs
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()
    return [dict(row) for row in rows]


def shorten(value: str | None, limit: int) -> str:
    if not value:
        return "-"
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)] + "…"


def print_counts(counts: list[dict[str, Any]]) -> None:
    if not counts:
        print("jobs: 0")
        return

    print("jobs by status:")
    for item in counts:
        print(f"  {item['status'] or 'UNKNOWN'}: {item['count']}")


def print_table(jobs: list[dict[str, Any]], details: bool) -> None:
    if not jobs:
        print("no jobs found")
        return

    if details:
        for job in jobs:
            print("")
            print(f"job_id: {job['job_id']}")
            print(f"  status: {job['status']}")
            print(f"  original_filename: {job['original_filename'] or '-'}")
            print(f"  current_step: {job['current_step'] or '-'}")
            print(f"  source_mode: {job['source_mode'] or '-'}")
            print(f"  attempt_type: {job['attempt_type'] or '-'}")
            print(f"  retry_of_job_id: {job['retry_of_job_id'] or '-'}")
            print(f"  created_at: {job['created_at'] or '-'}")
            print(f"  updated_at: {job['updated_at'] or '-'}")
            print(f"  discovered_from: {job['discovered_from'] or '-'}")
            print(f"  bronze_path: {job['bronze_path'] or '-'}")
            print(f"  silver_json_path: {job['silver_json_path'] or '-'}")
            print(f"  gold_result_dir: {job['gold_result_dir'] or '-'}")
            print(f"  failed_dir: {job['failed_dir'] or '-'}")
            print(f"  processing_dir: {job['processing_dir'] or '-'}")
            print(f"  error_message: {job['error_message'] or '-'}")
        return

    print("")
    print(f"{'updated_at':19}  {'status':20}  {'job_id':28}  {'file'}")
    print("-" * 100)
    for job in jobs:
        updated_at = shorten(job["updated_at"], 19)
        status = shorten(job["status"], 20)
        job_id = shorten(job["job_id"], 28)
        filename = shorten(job["original_filename"], 40)
        print(f"{updated_at:19}  {status:20}  {job_id:28}  {filename}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only status view for data/jobs.db.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--status")
    parser.add_argument("--job-id")
    parser.add_argument("--search")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        with connect(args.db_path) as connection:
            counts = fetch_counts(connection)
            jobs = fetch_jobs(connection, args)
    except Exception as exc:
        if args.json_output:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}")
        return 1

    payload = {
        "ok": True,
        "db_path": str(args.db_path),
        "counts": counts,
        "jobs": jobs,
    }

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print_counts(counts)
    print_table(jobs, details=args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())