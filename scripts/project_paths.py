from __future__ import annotations

import os
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"
HF_CACHE_DIR = PROJECT_ROOT / "hf_cache"

LANDING_DIR = DATA_DIR / "landing"
BRONZE_RAW_DIR = DATA_DIR / "bronze" / "raw_original"
PROCESSING_DIR = DATA_DIR / "processing"
SILVER_AUDIO_FLAC_DIR = DATA_DIR / "silver" / "audio_flac"
SILVER_ASR_JSON_DIR = DATA_DIR / "silver" / "asr_json"
GOLD_TRANSCRIPTS_DIR = DATA_DIR / "gold" / "transcripts"
FAILED_DIR = DATA_DIR / "failed"
ARCHIVE_DIR = DATA_DIR / "archive"

WHISPERX_CONFIG_PATH = CONFIG_DIR / "whisperx_config.json"
ENV_PATH = PROJECT_ROOT / ".env"


def resolve_project_path(value: str | None, default_path: Path) -> Path:
    """
    Делает путь переносимым:
    - absolute path остаётся absolute;
    - relative path считается относительно PROJECT_ROOT;
    - пустое значение заменяется на default_path.
    """
    if not value:
        return default_path

    path = Path(value)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def load_dotenv_if_exists(path: Path = ENV_PATH) -> None:
    """
    Минимальный .env loader без внешней зависимости python-dotenv.

    Поддерживает строки:
    KEY=value

    Не перезаписывает переменные, которые уже заданы в окружении.
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def get_hf_token() -> str | None:
    """
    Возвращает Hugging Face token из окружения или .env.

    Токен не должен храниться в config/*.json и не должен попадать в Git.
    """
    load_dotenv_if_exists()

    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("PYANNOTE_AUTH_TOKEN")
    )

    if token:
        return token.strip()

    return None


def apply_hf_environment(hf_cache_dir: Path | None = None) -> Path:
    """
    Выставляет Hugging Face cache/token окружение.

    Возвращает фактический путь к HF cache.
    """
    load_dotenv_if_exists()

    cache_dir = hf_cache_dir or HF_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("HF_HUB_CACHE", str(cache_dir / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir / "transformers"))

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    token = get_hf_token()

    if token:
        os.environ.setdefault("HF_TOKEN", token)
        os.environ.setdefault("HUGGINGFACE_HUB_TOKEN", token)

    return cache_dir