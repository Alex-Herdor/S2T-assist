from __future__ import annotations

from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

DIRS = [
    "data/landing",
    "data/bronze/raw_original",
    "data/processing",
    "data/silver/audio_flac",
    "data/silver/asr_json",
    "data/gold/transcripts",
    "data/failed",
    "data/archive",
    "hf_cache",
    "config",
]


def main() -> int:
    print(f"Project root: {PROJECT_ROOT}")
    print("Creating local directories...")

    for rel_path in DIRS:
        path = PROJECT_ROOT / rel_path
        path.mkdir(parents=True, exist_ok=True)

        gitkeep = path / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

        print(f"OK: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())