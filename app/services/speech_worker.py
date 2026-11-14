"""Killable local Whisper worker; no Telegram credentials or LLM tools used."""

import json
import os
import sys


def main():
    from faster_whisper import WhisperModel

    from app.core import config

    model = WhisperModel(os.getenv("WHISPER_MODEL", "base"), device="cpu", compute_type="int8",
                         cpu_threads=2, num_workers=1,
                         download_root=str(config.DATA_DIR / "whisper_models"))
    segments, _ = model.transcribe(sys.argv[1], beam_size=1, vad_filter=True)
    print(json.dumps({"text": " ".join(segment.text.strip() for segment in segments)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
