from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

LANDING_DIR = BASE_DIR / "data" / "landing"
PROCESS_ONE_SCRIPT = SCRIPT_DIR / "process_one_file.py"

ALLOWED_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
}

IGNORED_SUFFIXES = {
    ".done", ".uploading", ".part", ".tmp", ".crdownload",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def collect_landing_files(newest_first: bool = False) -> list[Path]:
    if not LANDING_DIR.exists():
        raise FileNotFoundError(f"Landing dir не найден: {LANDING_DIR}")

    files = [
        path for path in LANDING_DIR.iterdir()
        if is_candidate_file(path)
    ]

    return sorted(
        files,
        key=lambda p: p.stat().st_mtime,
        reverse=newest_first,
    )


def format_command(command: list[str]) -> str:
    return " ".join(
        f'"{x}"' if " " in str(x) else str(x)
        for x in command
    )


def run_one_file(
    input_path: Path,
    keep_processing: bool,
) -> int:
    command = [
        sys.executable,
        str(PROCESS_ONE_SCRIPT),
        "--input",
        str(input_path),
    ]

    if keep_processing:
        command.append("--keep-processing")

    print("")
    print("-" * 100)
    print(f"Запуск файла: {input_path}")
    print(f"Команда: {format_command(command)}")
    print("-" * 100)
    print("")

    result = subprocess.run(
        command,
        cwd=str(BASE_DIR),
    )

    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Однократная последовательная обработка всех готовых файлов из data\\landing."
    )

    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Не останавливаться на первой ошибке, а продолжать следующие файлы.",
    )

    parser.add_argument(
        "--keep-processing",
        action="store_true",
        help="Передать --keep-processing в process_one_file.py для отладки.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Обработать максимум N файлов за запуск.",
    )

    parser.add_argument(
        "--newest-first",
        action="store_true",
        help="Обрабатывать сначала самые новые файлы. По умолчанию сначала самые старые.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, какие файлы были бы обработаны.",
    )

    parser.add_argument(
        "--show-sizes",
        action="store_true",
        help="Показать размеры файлов в списке перед обработкой.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    files = collect_landing_files(newest_first=args.newest_first)

    if args.limit is not None:
        files = files[: args.limit]

    print("")
    print("=" * 100)
    print("PROCESS LANDING ONCE")
    print("=" * 100)
    print(f"started_at:        {now_iso()}")
    print(f"base_dir:          {BASE_DIR}")
    print(f"landing_dir:       {LANDING_DIR}")
    print(f"files_to_process:  {len(files)}")
    print(f"continue_on_error: {args.continue_on_error}")
    print(f"keep_processing:   {args.keep_processing}")
    print(f"dry_run:           {args.dry_run}")
    print("=" * 100)

    if not files:
        print("")
        print("В landing нет готовых файлов для обработки.")
        return 0

    print("")
    print("Файлы:")

    for index, path in enumerate(files, start=1):
        size_text = ""
        if args.show_sizes:
            size_text = f" | {human_size(path.stat().st_size)}"

        print(f"{index}. {path.name}{size_text}")

    if args.dry_run:
        print("")
        print("Dry-run: обработка не запускалась.")
        return 0

    success_count = 0
    failed_count = 0
    failed_items: list[tuple[Path, int]] = []

    for index, path in enumerate(files, start=1):
        if not path.exists():
            print("")
            print(f"WARNING: файл исчез до обработки, пропускаю: {path}")
            failed_count += 1
            failed_items.append((path, -1))

            if not args.continue_on_error:
                break

            continue

        print("")
        print("=" * 100)
        print(f"[{index}/{len(files)}] Обработка: {path.name}")
        print("=" * 100)

        exit_code = run_one_file(
            input_path=path,
            keep_processing=args.keep_processing,
        )

        if exit_code == 0:
            success_count += 1
            print("")
            print(f"[{index}/{len(files)}] SUCCESS: {path.name}")
        else:
            failed_count += 1
            failed_items.append((path, exit_code))

            print("")
            print(f"[{index}/{len(files)}] FAILED: {path.name} | exit_code={exit_code}")

            if not args.continue_on_error:
                print("")
                print("Остановка на первой ошибке.")
                print("Чтобы продолжать следующие файлы, используй --continue-on-error.")
                break

    print("")
    print("=" * 100)
    print("BATCH SUMMARY")
    print("=" * 100)
    print(f"finished_at:       {now_iso()}")
    print(f"planned:           {len(files)}")
    print(f"success:           {success_count}")
    print(f"failed/skipped:    {failed_count}")

    if failed_items:
        print("")
        print("Проблемные файлы:")
        for path, exit_code in failed_items:
            print(f"- {path.name} | exit_code={exit_code}")

    print("")
    print("Для проверки итогового состояния:")
    print("python scripts\\status_jobs.py")

    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())