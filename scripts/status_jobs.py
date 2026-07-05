from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

DATA_DIR = BASE_DIR / "data"
LANDING_DIR = DATA_DIR / "landing"
PROCESSING_DIR = DATA_DIR / "processing"
FAILED_DIR = DATA_DIR / "failed"
GOLD_TRANSCRIPTS_DIR = DATA_DIR / "gold" / "transcripts"
BRONZE_DIR = DATA_DIR / "bronze" / "raw_original"
SILVER_JSON_DIR = DATA_DIR / "silver" / "asr_json"
SILVER_FLAC_DIR = DATA_DIR / "silver" / "audio_flac"

ALLOWED_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
}

IGNORED_SUFFIXES = {
    ".done", ".uploading", ".part", ".tmp", ".crdownload",
}


def now() -> datetime:
    return datetime.now()


def safe_read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

        return {}
    except Exception:
        return {}


def read_retried_marker(failed_job_dir: Path) -> tuple[dict, bool]:
    marker_path = failed_job_dir / "RETRIED_SUCCESSFULLY.json"

    if not marker_path.exists():
        return {}, False

    marker = safe_read_json(marker_path)
    return marker, True


def path_exists(value) -> bool:
    if not value:
        return False

    try:
        return Path(value).exists()
    except Exception:
        return False


def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def dir_mtime(path: Path) -> float:
    mtimes = [file_mtime(path)]

    try:
        for child in path.rglob("*"):
            mtimes.append(file_mtime(child))
    except Exception:
        pass

    return max(mtimes) if mtimes else 0.0


def human_age_from_ts(ts: float) -> str:
    if not ts:
        return "unknown"

    delta = now() - datetime.fromtimestamp(ts)
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"

    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"

    days = hours // 24
    return f"{days}d"


def human_size(num_bytes: int | float | None) -> str:
    if not num_bytes:
        return "0 B"

    size = float(num_bytes)

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} PB"


def calc_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except Exception:
            return 0

    total = 0

    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except Exception:
                    pass
    except Exception:
        pass

    return total


def truncate(value, max_len: int = 120) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")

    if len(text) <= max_len:
        return text

    return text[: max_len - 3] + "..."


def is_candidate_landing_file(path: Path) -> bool:
    if not path.is_file():
        return False

    if path.name.startswith("."):
        return False

    if path.suffix.lower() in IGNORED_SUFFIXES:
        return False

    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False

    return True


def collect_landing(include_sizes: bool) -> list[dict]:
    items = []

    if not LANDING_DIR.exists():
        return items

    for path in LANDING_DIR.iterdir():
        if not path.is_file():
            continue

        status = "ready" if is_candidate_landing_file(path) else "ignored"

        item = {
            "status": status,
            "filename": path.name,
            "path": str(path),
            "age": human_age_from_ts(file_mtime(path)),
            "mtime": file_mtime(path),
        }

        if include_sizes:
            item["size"] = calc_size(path)

        items.append(item)

    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def load_gold_context(result_dir: Path) -> tuple[dict, str]:
    context_path = result_dir / "job_context.json"
    manifest_path = result_dir / "manifest.json"

    if context_path.exists():
        return safe_read_json(context_path), "job_context"

    if manifest_path.exists():
        return safe_read_json(manifest_path), "manifest"

    return {}, "missing"


def collect_gold(include_sizes: bool) -> list[dict]:
    items = []

    if not GOLD_TRANSCRIPTS_DIR.exists():
        return items

    for result_dir in GOLD_TRANSCRIPTS_DIR.iterdir():
        if not result_dir.is_dir():
            continue

        context, source = load_gold_context(result_dir)

        paths = context.get("paths", {})
        if not isinstance(paths, dict):
            paths = {}

        raw_json_path = result_dir / "whisperx_raw.json"
        manifest_path = result_dir / "manifest.json"
        job_context_path = result_dir / "job_context.json"

        raw_json_info = context.get("raw_json_info", {})
        if not isinstance(raw_json_info, dict):
            raw_json_info = {}

        job_id = context.get("job_id") or result_dir.name
        original_filename = context.get("original_filename")
        attempt_type = context.get("attempt_type")
        retry_of_job_id = context.get("retry_of_job_id")

        item = {
            "job_id": job_id,
            "original_filename": original_filename,
            "result_dir_name": result_dir.name,
            "result_dir": str(result_dir),
            "context_source": source,
            "attempt_type": attempt_type,
            "retry_of_job_id": retry_of_job_id,
            "raw_json_exists": raw_json_path.exists(),
            "manifest_exists": manifest_path.exists(),
            "job_context_exists": job_context_path.exists(),
            "word_count": raw_json_info.get("word_count"),
            "words_with_timings": raw_json_info.get("words_with_timings"),
            "has_word_timings": raw_json_info.get("has_word_timings"),
            "mtime": dir_mtime(result_dir),
            "age": human_age_from_ts(dir_mtime(result_dir)),
        }

        if include_sizes:
            item["size"] = calc_size(result_dir)

        items.append(item)

    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def build_retried_map(gold_items: list[dict]) -> dict[str, list[dict]]:
    retried_map: dict[str, list[dict]] = {}

    for item in gold_items:
        old_job_id = item.get("retry_of_job_id")

        if not old_job_id:
            continue

        retried_map.setdefault(old_job_id, []).append(item)

    return retried_map


def collect_failed(gold_items: list[dict], include_sizes: bool) -> list[dict]:
    items = []

    if not FAILED_DIR.exists():
        return items

    retried_map = build_retried_map(gold_items)

    for failed_job_dir in FAILED_DIR.iterdir():
        if not failed_job_dir.is_dir():
            continue

        context_path = failed_job_dir / "processing" / "job_context.json"
        context = safe_read_json(context_path)

        paths = context.get("paths", {})
        if not isinstance(paths, dict):
            paths = {}

        job_id = context.get("job_id") or failed_job_dir.name
        retried_by = retried_map.get(job_id, [])
            
        retried_marker, retried_marker_exists = read_retried_marker(failed_job_dir)

        if retried_marker_exists:
            failed_lifecycle_status = "retried_successfully_marked"
        elif retried_by:
            failed_lifecycle_status = "retried_successfully_inferred"
        else:
            failed_lifecycle_status = "failed_active"

        bronze_path = paths.get("bronze_raw_original")
        silver_json_path = paths.get("silver_asr_json")
        failed_step = context.get("failed_step") or context.get("current_step")
        error_message = context.get("error_message")

        error_file = failed_job_dir / "ERROR.txt"

        if not error_message and error_file.exists():
            try:
                lines = error_file.read_text(encoding="utf-8", errors="replace").splitlines()
                non_empty = [x.strip() for x in lines if x.strip()]
                error_message = non_empty[-1] if non_empty else None
            except Exception:
                pass

        item = {
            "job_id": job_id,
            "failed_dir_name": failed_job_dir.name,
            "failed_dir": str(failed_job_dir),
            "original_filename": context.get("original_filename"),
            "failed_step": failed_step,
            "current_step": context.get("current_step"),
            "error_message": error_message,
            "context_exists": context_path.exists(),
            "error_file_exists": error_file.exists(),
            "bronze_exists": path_exists(bronze_path),
            "bronze_path": bronze_path,
            "silver_json_exists": path_exists(silver_json_path),
            "silver_json_path": silver_json_path,
            "retried": bool(retried_by),
            "retried_by_job_ids": [x.get("job_id") for x in retried_by],
            "retried_result_dirs": [x.get("result_dir") for x in retried_by],
            "failed_lifecycle_status": failed_lifecycle_status,
            "retried_marker_exists": retried_marker_exists,
            "retried_marker_status": retried_marker.get("status"),
            "retried_marker_retry_source": retried_marker.get("retry_source"),
            "retried_marker_latest_retry_job_id": (
                retried_marker.get("latest_retry", {}).get("new_job_id")
                if isinstance(retried_marker.get("latest_retry"), dict)
                else None
            ),
            "mtime": dir_mtime(failed_job_dir),
            "age": human_age_from_ts(dir_mtime(failed_job_dir)),
        }

        if include_sizes:
            item["size"] = calc_size(failed_job_dir)

        items.append(item)

    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def collect_processing(include_sizes: bool, orphan_hours: int) -> list[dict]:
    items = []

    if not PROCESSING_DIR.exists():
        return items

    orphan_seconds = orphan_hours * 3600

    for job_dir in PROCESSING_DIR.iterdir():
        if not job_dir.is_dir():
            continue

        context_path = job_dir / "job_context.json"
        context = safe_read_json(context_path)

        cleanup_marker = job_dir / "CLEANUP_FAILED.txt"
        last_mtime = dir_mtime(job_dir)
        age_seconds = (now() - datetime.fromtimestamp(last_mtime)).total_seconds() if last_mtime else 0

        if cleanup_marker.exists():
            processing_status = "cleanup_failed"
        elif age_seconds >= orphan_seconds:
            processing_status = "orphan_candidate"
        else:
            processing_status = "processing_or_recent"

        item = {
            "job_id": context.get("job_id") or job_dir.name,
            "processing_dir_name": job_dir.name,
            "processing_dir": str(job_dir),
            "processing_status": processing_status,
            "current_step": context.get("current_step"),
            "status": context.get("status"),
            "original_filename": context.get("original_filename"),
            "context_exists": context_path.exists(),
            "cleanup_failed": cleanup_marker.exists(),
            "mtime": last_mtime,
            "age": human_age_from_ts(last_mtime),
        }

        if include_sizes:
            item["size"] = calc_size(job_dir)

        items.append(item)

    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def print_header(title: str, count: int) -> None:
    print("")
    print("=" * 100)
    print(f"{title} ({count})")
    print("=" * 100)


def print_landing(items: list[dict], limit: int, include_sizes: bool) -> None:
    print_header("LANDING", len(items))

    if not items:
        print("Пусто.")
        return

    for item in items[:limit]:
        size = f" | {human_size(item.get('size'))}" if include_sizes else ""
        print(
            f"[{item['status']}] {item['filename']} "
            f"| age={item['age']}{size}"
        )

    if len(items) > limit:
        print(f"... ещё {len(items) - limit}")


def print_processing(items: list[dict], limit: int, include_sizes: bool) -> None:
    print_header("PROCESSING", len(items))

    if not items:
        print("Пусто.")
        return

    for item in items[:limit]:
        size = f" | {human_size(item.get('size'))}" if include_sizes else ""
        print(
            f"[{item['processing_status']}] {item['job_id']} "
            f"| step={item.get('current_step')} "
            f"| original={item.get('original_filename')} "
            f"| age={item['age']}{size}"
        )

        if item["cleanup_failed"]:
            print(f"  marker: {item['processing_dir']}\\CLEANUP_FAILED.txt")

        if not item["context_exists"]:
            print("  WARNING: нет job_context.json")

    if len(items) > limit:
        print(f"... ещё {len(items) - limit}")


def print_failed(items: list[dict], limit: int, include_sizes: bool) -> None:
    print_header("FAILED", len(items))

    if not items:
        print("Пусто.")
        return

    for item in items[:limit]:
        size = f" | {human_size(item.get('size'))}" if include_sizes else ""

        retried_text = "yes" if item["retried"] or item["retried_marker_exists"] else "no"
        lifecycle = item.get("failed_lifecycle_status") or "unknown"

        print(
            f"[{lifecycle}] {item['job_id']} "
            f"| step={item.get('failed_step')} "
            f"| retried={retried_text} "
            f"| bronze={'yes' if item['bronze_exists'] else 'NO'} "
            f"| original={item.get('original_filename')} "
            f"| age={item['age']}{size}"
        )

        if item["retried_marker_exists"]:
            print(
                "  marker: RETRIED_SUCCESSFULLY.json"
                f" | source={item.get('retried_marker_retry_source')}"
                f" | latest_retry={item.get('retried_marker_latest_retry_job_id')}"
            )

        if item["retried"]:
            print(f"  retried_by: {', '.join([x for x in item['retried_by_job_ids'] if x])}")

        if item.get("error_message"):
            print(f"  error: {truncate(item['error_message'], 180)}")

        if not item["context_exists"]:
            print("  WARNING: нет processing\\job_context.json")

        if not item["bronze_exists"]:
            print(f"  WARNING: bronze не найден: {item.get('bronze_path')}")

    if len(items) > limit:
        print(f"... ещё {len(items) - limit}")


def print_gold(items: list[dict], limit: int, include_sizes: bool) -> None:
    print_header("GOLD / SUCCESS", len(items))

    if not items:
        print("Пусто.")
        return

    for item in items[:limit]:
        size = f" | {human_size(item.get('size'))}" if include_sizes else ""

        flags = []
        if not item["raw_json_exists"]:
            flags.append("NO_RAW_JSON")
        if not item["manifest_exists"]:
            flags.append("NO_MANIFEST")
        if not item["job_context_exists"]:
            flags.append("NO_JOB_CONTEXT")
        if item.get("has_word_timings") is False:
            flags.append("NO_WORD_TIMINGS")

        flag_text = f" | flags={','.join(flags)}" if flags else ""

        retry_text = ""
        if item.get("retry_of_job_id"):
            retry_text = (
                f" | attempt={item.get('attempt_type')}"
                f" | retry_of={item.get('retry_of_job_id')}"
            )

        print(
            f"[success] {item['job_id']} "
            f"| original={item.get('original_filename')} "
            f"| words={item.get('words_with_timings')} "
            f"| age={item['age']}{retry_text}{flag_text}{size}"
        )
        print(f"  result: {item['result_dir_name']}")

    if len(items) > limit:
        print(f"... ещё {len(items) - limit}")


def print_summary(
    landing: list[dict],
    processing: list[dict],
    failed: list[dict],
    gold: list[dict],
) -> None:
    landing_ready = [x for x in landing if x["status"] == "ready"]
    landing_ignored = [x for x in landing if x["status"] != "ready"]

    cleanup_failed = [x for x in processing if x["processing_status"] == "cleanup_failed"]
    orphan_candidates = [x for x in processing if x["processing_status"] == "orphan_candidate"]

    failed_active = [
        x for x in failed
        if x.get("failed_lifecycle_status") == "failed_active"
    ]

    failed_retried_marked = [
        x for x in failed
        if x.get("failed_lifecycle_status") == "retried_successfully_marked"
    ]

    failed_retried_inferred = [
        x for x in failed
        if x.get("failed_lifecycle_status") == "retried_successfully_inferred"
    ]

    failed_retried = failed_retried_marked + failed_retried_inferred

    gold_with_warnings = [
        x for x in gold
        if not x["raw_json_exists"]
        or not x["manifest_exists"]
        or not x["job_context_exists"]
        or x.get("has_word_timings") is False
    ]

    print("")
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"landing ready:       {len(landing_ready)}")
    print(f"landing ignored:     {len(landing_ignored)}")
    print(f"processing dirs:     {len(processing)}")
    print(f"cleanup failed:      {len(cleanup_failed)}")
    print(f"orphan candidates:   {len(orphan_candidates)}")
    print(f"failed total:        {len(failed)}")
    print(f"failed active:       {len(failed_active)}")
    print(f"failed retried:      {len(failed_retried)}")
    print(f"  marked:            {len(failed_retried_marked)}")
    print(f"  inferred:          {len(failed_retried_inferred)}")
    print(f"gold success:        {len(gold)}")
    print(f"gold warnings:       {len(gold_with_warnings)}")


def build_report(include_sizes: bool, orphan_hours: int) -> dict:
    landing = collect_landing(include_sizes=include_sizes)
    gold = collect_gold(include_sizes=include_sizes)
    failed = collect_failed(gold_items=gold, include_sizes=include_sizes)
    processing = collect_processing(include_sizes=include_sizes, orphan_hours=orphan_hours)

    return {
        "generated_at": now().isoformat(timespec="seconds"),
        "base_dir": str(BASE_DIR),
        "landing": landing,
        "processing": processing,
        "failed": failed,
        "gold": gold,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only статус локального пайплайна WhisperX."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Сколько элементов показывать в каждой секции.",
    )

    parser.add_argument(
        "--sizes",
        action="store_true",
        help="Посчитать размер папок/файлов. Может быть медленно на больших архивах.",
    )

    parser.add_argument(
        "--orphan-hours",
        type=int,
        default=6,
        help="Через сколько часов processing job считать кандидатом в orphan.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести полный отчёт в JSON.",
    )

    parser.add_argument("--landing-only", action="store_true")
    parser.add_argument("--processing-only", action="store_true")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--gold-only", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    report = build_report(
        include_sizes=args.sizes,
        orphan_hours=args.orphan_hours,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    landing = report["landing"]
    processing = report["processing"]
    failed = report["failed"]
    gold = report["gold"]

    any_filter = (
        args.landing_only
        or args.processing_only
        or args.failed_only
        or args.gold_only
    )

    if not any_filter:
        print_summary(
            landing=landing,
            processing=processing,
            failed=failed,
            gold=gold,
        )

    if args.landing_only or not any_filter:
        print_landing(landing, limit=args.limit, include_sizes=args.sizes)

    if args.processing_only or not any_filter:
        print_processing(processing, limit=args.limit, include_sizes=args.sizes)

    if args.failed_only or not any_filter:
        print_failed(failed, limit=args.limit, include_sizes=args.sizes)

    if args.gold_only or not any_filter:
        print_gold(gold, limit=args.limit, include_sizes=args.sizes)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())