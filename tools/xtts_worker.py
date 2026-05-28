#!/usr/bin/env python3
"""
XTTS v2 inference worker — runs inside the Python 3.11 venv.

Called by speech.py as a subprocess:
    <venv>/bin/python3 tools/xtts_worker.py <ref_wav> <text> <out_wav>

Exits 0 on success, 1 on failure.
Writes synthesised audio to <out_wav>.
"""

import sys
import time

def main() -> None:
    if len(sys.argv) != 4:
        print("Usage: xtts_worker.py <ref_wav> <text> <out_wav>", file=sys.stderr)
        sys.exit(1)

    ref_wav, text, out_wav = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        from TTS.api import TTS
    except ImportError as e:
        print(f"TTS not installed in this venv: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        t0 = time.monotonic()
        model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
        model.tts_to_file(text=text, speaker_wav=ref_wav, language="en", file_path=out_wav)
        elapsed = time.monotonic() - t0
        print(f"ok:{elapsed:.2f}", flush=True)
    except Exception as e:
        print(f"error:{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
