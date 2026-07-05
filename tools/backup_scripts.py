from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
BACKUP_ROOT = PROJECT_ROOT / ".local_backups"

DEFAULT_PATTERNS = [
    "scripts/*.py",
    "tests/*.py",
]

DOCS_AND_SAFE_CONFIG_PATTERNS = [
    "README.md",
    "docs/*.md",
    "config/*.example.json",
    ".env.example",
    ".gitignore",
    "environment.yml",
]


def sanitize_label(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-zA-Z0-9а-яА-Я_-]+", "_", value)
    value = value.strip("_")

    return value or "manual"


def collect_files(include_docs: bool) -> list[Path]:
    patterns = list(DEFAULT_PATTERNS)

    if include_docs:
        patterns.extend(DOCS_AND_SAFE_CONFIG_PATTERNS)

    files: list[Path] = []

    for pattern in patterns:
        files.extend(PROJECT_ROOT.glob(pattern))

    unique_files = sorted(
        {
            path.resolve()
            for path in files
            if path.exists() and path.is_file()
        }
    )

    return unique_files


def make_backup_dir(label: str | None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if label:
        safe_label = sanitize_label(label)
        name = f"scripts_{timestamp}_{safe_label}"
    else:
        name = f"scripts_{timestamp}"

    return BACKUP_ROOT / name


def copy_files(files: list[Path], backup_dir: Path) -> list[dict]:
    copied: list[dict] = []

    for source_path in files:
        rel_path = source_path.relative_to(PROJECT_ROOT)
        target_path = backup_dir / rel_path

        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

        copied.append(
            {
                "source": str(rel_path),
                "target": str(target_path.relative_to(PROJECT_ROOT)),
                "size_bytes": source_path.stat().st_size,
            }
        )

    return copied


def write_manifest(
    backup_dir: Path,
    copied: list[dict],
    include_docs: bool,
    label: str | None,
) -> Path:
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "backup_dir": str(backup_dir),
        "label": label,
        "include_docs": include_docs,
        "files_count": len(copied),
        "files": copied,
    }

    manifest_path = backup_dir / "backup_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return manifest_path


def print_files(files: list[Path]) -> None:
    if not files:
        print("Файлы для backup не найдены.")
        return

    print("Файлы для backup:")

    for path in files:
        print(f"  {path.relative_to(PROJECT_ROOT)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Локальный backup Python-скриптов проекта перед ручными правками. "
            "Backup складывается в .local_backups и не должен попадать в Git."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--include-docs",
        action="store_true",
        help=(
            "Дополнительно сохранить README.md, docs/*.md, .env.example, "
            "config/*.example.json, .gitignore и environment.yml."
        ),
    )

    parser.add_argument(
        "--label",
        default=None,
        help="Короткая метка backup'а, например before_pipeline_edit.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать, что будет сохранено, но не копировать файлы.",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="Только вывести список файлов для backup.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    files = collect_files(include_docs=args.include_docs)

    if args.list or args.dry_run:
        print_files(files)

    if args.list:
        return 0

    backup_dir = make_backup_dir(label=args.label)

    if args.dry_run:
        print("")
        print(f"Dry-run: backup был бы создан здесь: {backup_dir}")
        return 0

    if not files:
        print("Файлы для backup не найдены. Backup не создан.")
        return 1

    backup_dir.mkdir(parents=True, exist_ok=False)

    copied = copy_files(files, backup_dir)
    manifest_path = write_manifest(
        backup_dir=backup_dir,
        copied=copied,
        include_docs=args.include_docs,
        label=args.label,
    )

    print("")
    print("=" * 100)
    print("BACKUP CREATED")
    print("=" * 100)
    print(f"backup_dir: {backup_dir}")
    print(f"manifest:   {manifest_path}")
    print(f"files:      {len(copied)}")
    print("=" * 100)

    for item in copied:
        print(f"OK: {item['source']}")

    print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())