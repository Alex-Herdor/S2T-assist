import argparse
import json
import re
from pathlib import Path


def format_ts(seconds: float) -> str:
    seconds = max(0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def build_turns_from_segments(
    segments: list[dict],
    max_pause_sec: float = 1.2,
    min_text_len: int = 1
) -> list[dict]:
    turns = []

    current = None

    for seg in segments:
        text = clean_text(seg.get("text", ""))
        if len(text) < min_text_len:
            continue

        speaker = seg.get("speaker") or "UNKNOWN"
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))

        if current is None:
            current = {
                "speaker": speaker,
                "start": start,
                "end": end,
                "texts": [text],
            }
            continue

        pause = start - current["end"]
        same_speaker = speaker == current["speaker"]
        short_pause = pause <= max_pause_sec

        if same_speaker and short_pause:
            current["end"] = end
            current["texts"].append(text)
        else:
            turns.append(current)
            current = {
                "speaker": speaker,
                "start": start,
                "end": end,
                "texts": [text],
            }

    if current is not None:
        turns.append(current)

    return turns


def write_txt(turns: list[dict], output_path: Path):
    lines = []

    for turn in turns:
        start = format_ts(turn["start"])
        end = format_ts(turn["end"])
        speaker = turn["speaker"]
        text = clean_text(" ".join(turn["texts"]))

        lines.append(f"[{start}–{end}] {speaker}:")
        lines.append(text)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_md(turns: list[dict], output_path: Path):
    lines = ["# Расшифровка", ""]

    for turn in turns:
        start = format_ts(turn["start"])
        end = format_ts(turn["end"])
        speaker = turn["speaker"]
        text = clean_text(" ".join(turn["texts"]))

        lines.append(f"## {speaker} · {start}–{end}")
        lines.append("")
        lines.append(text)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Преобразование WhisperX JSON в читаемую стенограмму по репликам"
    )

    parser.add_argument(
        "json_path",
        help="Путь к JSON-файлу WhisperX"
    )

    parser.add_argument(
        "--pause",
        type=float,
        default=1.2,
        help="Пауза в секундах, после которой начинается новая реплика"
    )

    args = parser.parse_args()

    json_path = Path(args.json_path).resolve()

    if not json_path.exists():
        raise FileNotFoundError(f"Не найден файл: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("segments", [])

    if not segments:
        raise ValueError("В JSON не найден массив segments")

    turns = build_turns_from_segments(
        segments=segments,
        max_pause_sec=args.pause
    )

    txt_path = json_path.with_name(json_path.stem + "_turns.txt")
    md_path = json_path.with_name(json_path.stem + "_turns.md")

    write_txt(turns, txt_path)
    write_md(turns, md_path)

    print(f"Готово:")
    print(f"TXT: {txt_path}")
    print(f"MD:  {md_path}")
    print(f"Реплик: {len(turns)}")


if __name__ == "__main__":
    main()