from __future__ import annotations

import argparse
import json
import py_compile
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"

PIPELINE_SCRIPT = SCRIPT_DIR / "pipeline.py"
CHECK_STORAGE_INTEGRITY_SCRIPT = SCRIPT_DIR / "check_storage_integrity.py"

SCRIPTS_TO_COMPILE = [
    SCRIPT_DIR / "project_paths.py",
    SCRIPT_DIR / "run_whisperx.py",
    SCRIPT_DIR / "process_one_file.py",
    SCRIPT_DIR / "process_landing_once.py",
    SCRIPT_DIR / "status_jobs.py",
    SCRIPT_DIR / "retry_failed_job.py",
    SCRIPT_DIR / "repair_gold_from_json.py",
    CHECK_STORAGE_INTEGRITY_SCRIPT,
    SCRIPT_DIR / "recover_orphaned_processing.py",
    SCRIPT_DIR / "cleanup_old_jobs.py",
    SCRIPT_DIR / "init_dirs.py",
    SCRIPT_DIR / "pipeline.py",
]

PIPELINE_HELP_COMMANDS = [
    [sys.executable, str(PIPELINE_SCRIPT), "--help"],
    [sys.executable, str(PIPELINE_SCRIPT), "process", "--help"],
    [sys.executable, str(PIPELINE_SCRIPT), "status", "--help"],
    [sys.executable, str(PIPELINE_SCRIPT), "repair", "--help"],
    [sys.executable, str(PIPELINE_SCRIPT), "doctor", "--help"],
    [sys.executable, str(PIPELINE_SCRIPT), "init", "--help"],
]


@dataclass
class SmokeResult:
    name: str
    status: str
    command: str | None = None
    details: str | None = None
    returncode: int | None = None


def command_to_text(command: list[str]) -> str:
    return subprocess.list2cmdline([str(x) for x in command])


def run_command(
    name: str,
    command: list[str],
    verbose: bool = False,
) -> SmokeResult:
    result = subprocess.run(
        [str(x) for x in command],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    details_parts = []

    if result.stdout and (verbose or result.returncode != 0):
        details_parts.append("STDOUT:\n" + result.stdout.strip())

    if result.stderr and (verbose or result.returncode != 0):
        details_parts.append("STDERR:\n" + result.stderr.strip())

    details = "\n\n".join(details_parts) if details_parts else None

    return SmokeResult(
        name=name,
        status="ok" if result.returncode == 0 else "error",
        command=command_to_text(command),
        details=details,
        returncode=result.returncode,
    )


def compile_script(script_path: Path) -> SmokeResult:
    try:
        py_compile.compile(str(script_path), doraise=True)

        return SmokeResult(
            name=f"py_compile:{script_path.name}",
            status="ok",
            command=f"python -m py_compile {script_path}",
        )
    except Exception as exc:
        return SmokeResult(
            name=f"py_compile:{script_path.name}",
            status="error",
            command=f"python -m py_compile {script_path}",
            details=str(exc),
            returncode=1,
        )


def collect_smoke_results(
    skip_doctor: bool,
    skip_integrity: bool,
    verbose: bool,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []

    for script_path in SCRIPTS_TO_COMPILE:
        results.append(compile_script(script_path))

    for command in PIPELINE_HELP_COMMANDS:
        results.append(
            run_command(
                name="help:" + " ".join(command[2:]),
                command=command,
                verbose=verbose,
            )
        )

    runtime_commands = [
        (
            "pipeline:init",
            [sys.executable, str(PIPELINE_SCRIPT), "init"],
        ),
        (
            "pipeline:status_json",
            [sys.executable, str(PIPELINE_SCRIPT), "status", "--json"],
        ),
        (
            "pipeline:process_dry_run",
            [
                sys.executable,
                str(PIPELINE_SCRIPT),
                "process",
                "--dry-run",
                "--show-sizes",
            ],
        ),
    ]
    
    if not skip_integrity:
        runtime_commands.append(
            (
                "storage:integrity",
                [sys.executable, str(CHECK_STORAGE_INTEGRITY_SCRIPT)],
            )
        )

    if not skip_doctor:
        runtime_commands.append(
            (
                "pipeline:doctor",
                [sys.executable, str(PIPELINE_SCRIPT), "doctor"],
            )
        )

    for name, command in runtime_commands:
        results.append(
            run_command(
                name=name,
                command=command,
                verbose=verbose,
            )
        )

    return results


def summarize(results: list[SmokeResult]) -> dict[str, int]:
    return {
        "ok": sum(1 for item in results if item.status == "ok"),
        "error": sum(1 for item in results if item.status == "error"),
    }


def print_results(
    results: list[SmokeResult],
    json_output: bool,
    verbose: bool,
) -> None:
    summary = summarize(results)

    if json_output:
        payload = {
            "project_root": str(PROJECT_ROOT),
            "summary": summary,
            "checks": [
                {
                    "name": item.name,
                    "status": item.status,
                    "command": item.command,
                    "returncode": item.returncode,
                    "details": item.details,
                }
                for item in results
            ],
        }

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("")
    print("=" * 100)
    print("PIPELINE SMOKE TESTS")
    print("=" * 100)
    print(f"project_root: {PROJECT_ROOT}")
    print(f"summary: ok={summary['ok']} error={summary['error']}")
    print("=" * 100)

    for item in results:
        if item.status == "ok" and not verbose:
            continue

        prefix = "OK" if item.status == "ok" else "ERROR"
        print(f"[{prefix}] {item.name}")

        if item.command:
            print(f"       command: {item.command}")

        if item.returncode is not None:
            print(f"       returncode: {item.returncode}")

        if item.details:
            print(item.details)

    if summary["error"] == 0 and not verbose:
        print("[OK] Smoke tests passed.")

    print("")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke tests для разработки локального WhisperX pipeline. "
            "Не запускает реальную транскрибацию."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Не запускать pipeline.py doctor. Полезно для CI или окружений без WhisperX/ffmpeg.",
    )
    
    parser.add_argument(
        "--skip-integrity",
        action="store_true",
        help=(
            "Не запускать check_storage_integrity.py. "
            "Полезно, если локальные данные временно находятся в переходном состоянии."
        ),
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести результат в JSON.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Печатать stdout/stderr успешных команд.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    results = collect_smoke_results(
        skip_doctor=args.skip_doctor,
        skip_integrity=args.skip_integrity,
        verbose=args.verbose,
    )

    print_results(
        results=results,
        json_output=args.json,
        verbose=args.verbose,
    )

    summary = summarize(results)

    return 1 if summary["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())