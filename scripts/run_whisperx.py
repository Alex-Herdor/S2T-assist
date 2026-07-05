import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from project_paths import (
    HF_CACHE_DIR,
    PROJECT_ROOT,
    WHISPERX_CONFIG_PATH,
    apply_hf_environment,
    get_hf_token as get_configured_hf_token,
    resolve_project_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

DEFAULT_CONFIG_PATH = WHISPERX_CONFIG_PATH
DEFAULT_HF_HOME = HF_CACHE_DIR


def setup_env(config: dict | None = None) -> None:
    config = config or {}

    hf_cache_dir = resolve_project_path(
        config.get("hf_cache_dir"),
        DEFAULT_HF_HOME,
    )

    apply_hf_environment(hf_cache_dir)

    threads = str(config.get("threads", 16))

    os.environ.setdefault("OMP_NUM_THREADS", threads)
    os.environ.setdefault("MKL_NUM_THREADS", threads)
    os.environ.setdefault("NUMEXPR_NUM_THREADS", threads)
    os.environ.setdefault("TORCH_NUM_THREADS", threads)


def add_arg(cmd: list[str], name: str, value) -> None:
    if value is None:
        return

    if isinstance(value, str) and value.strip() == "":
        return

    cmd.extend([name, str(value)])


def add_bool_value_arg(cmd: list[str], name: str, value) -> None:
    if value is None:
        return

    if isinstance(value, bool):
        cmd.extend([name, "True" if value else "False"])
    else:
        cmd.extend([name, str(value)])


def read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Не найден конфиг: {path}")

    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def resolve_path(path_value: str | None, default_path: Path, relative_to: Path) -> Path:
    if path_value is None or str(path_value).strip() == "":
        path = default_path
    else:
        path = Path(path_value)

    if not path.is_absolute():
        path = relative_to / path

    return path.resolve()


def get_hf_token() -> str | None:
    hf_token = get_configured_hf_token()

    if hf_token:
        return hf_token.strip()

    hf_home = Path(os.environ.get("HF_HOME", str(DEFAULT_HF_HOME)))
    token_path = Path(os.environ.get("HF_TOKEN_PATH", str(hf_home / "token")))

    if token_path.exists():
        return token_path.read_text(encoding="utf-8").strip()

    return None


def build_command(input_path: Path, run_output_dir: Path, config: dict) -> list[str]:
    whisperx_exe = shutil.which("whisperx")

    if whisperx_exe is None:
        raise RuntimeError(
            "Команда whisperx не найдена. "
            "Активируй окружение: conda activate whisperx-ru"
        )

    cmd = [
        whisperx_exe,
        str(input_path),
    ]

    add_arg(cmd, "--language", config.get("language", "ru"))
    add_arg(cmd, "--task", config.get("task", "transcribe"))
    add_arg(cmd, "--model", config.get("model", "large-v3"))
    add_arg(cmd, "--device", config.get("device", "cpu"))
    add_arg(cmd, "--compute_type", config.get("compute_type", "int8"))
    add_arg(cmd, "--batch_size", config.get("batch_size", 4))
    add_arg(cmd, "--threads", config.get("threads", 16))
    add_arg(cmd, "--align_model", config.get("align_model"))
    add_arg(cmd, "--output_format", config.get("output_format", "json"))
    add_arg(cmd, "--output_dir", run_output_dir)

    add_bool_value_arg(cmd, "--print_progress", config.get("print_progress", True))

    add_arg(cmd, "--vad_method", config.get("vad_method"))
    add_arg(cmd, "--vad_onset", config.get("vad_onset"))
    add_arg(cmd, "--vad_offset", config.get("vad_offset"))

    diarize = bool(config.get("diarize", False))

    if diarize:
        cmd.append("--diarize")

        hf_token = get_hf_token()

        if not hf_token:
            raise RuntimeError(
                "В конфиге включена diarization, но не найден Hugging Face token.\n"
                "Для MVP можно отключить diarization в локальном config\\whisperx_config.json:\n"
                '  "diarize": false\n'
                "\n"
                "Или задать токен одним из способов:\n"
                "  1. В текущей консоли: set HF_TOKEN=hf_your_token\n"
                "  2. В локальном файле .env: HF_TOKEN=hf_your_token\n"
                "\n"
                "Файл .env не должен попадать в Git."
            )

        add_arg(cmd, "--hf_token", hf_token)
        add_arg(cmd, "--min_speakers", config.get("min_speakers"))
        add_arg(cmd, "--max_speakers", config.get("max_speakers"))

    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Запуск WhisperX по настройкам из config\\whisperx_config.json"
    )

    parser.add_argument(
        "input_file",
        help="Имя файла из input_dir или полный путь к файлу",
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Путь к JSON-конфигу. По умолчанию: config\\whisperx_config.json внутри PROJECT_ROOT.",
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Папка для технического вывода WhisperX. "
            "Если не указана, используется <текущая_папка>\\whisperx_output."
        ),
    )

    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = read_json(config_path)

    setup_env(config)

    cwd = Path.cwd().resolve()

    input_dir = resolve_path(
        config.get("input_dir"),
        default_path=BASE_DIR / "data" / "landing",
        relative_to=BASE_DIR,
    )

    input_file = Path(args.input_file)

    if input_file.is_absolute():
        input_path = input_file
    else:
        input_path = input_dir / input_file

    input_path = input_path.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Не найден входной файл: {input_path}")

    output_dir = resolve_path(
        args.output_dir,
        default_path=cwd / "whisperx_output",
        relative_to=cwd,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    run_output_dir = output_dir / input_path.stem
    run_output_dir.mkdir(parents=True, exist_ok=True)

    diarize = bool(config.get("diarize", False))

    cmd = build_command(
        input_path=input_path,
        run_output_dir=run_output_dir,
        config=config,
    )

    print()
    print("=== WhisperX запуск ===")
    print(f"Project root:      {BASE_DIR}")
    print(f"Config:            {config_path}")
    print(f"Входной файл:      {input_path}")
    print(f"Папка результата:  {run_output_dir}")
    print(f"Модель:            {config.get('model', 'large-v3')}")
    print(f"Язык:              {config.get('language', 'ru')}")
    print(f"Диаризация:        {diarize}")
    print()
    print("=== CPU / HF env ===")
    print(f"HF_HOME:           {os.environ.get('HF_HOME')}")
    print(f"HF_HUB_CACHE:      {os.environ.get('HF_HUB_CACHE')}")
    print(f"TRANSFORMERS_CACHE:{os.environ.get('TRANSFORMERS_CACHE')}")
    print(f"OMP_NUM_THREADS:   {os.environ.get('OMP_NUM_THREADS')}")
    print(f"MKL_NUM_THREADS:   {os.environ.get('MKL_NUM_THREADS')}")
    print(f"TORCH_NUM_THREADS: {os.environ.get('TORCH_NUM_THREADS')}")
    print()

    result = subprocess.run(cmd)

    if result.returncode != 0:
        return result.returncode

    print()
    print("=== Готово ===")
    print(f"Результаты сохранены в: {run_output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())