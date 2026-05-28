#!/usr/bin/env bash
# Sets up the Python 3.11 venv for XTTS v2 voice cloning.
# Run once: bash tools/setup_xtts_venv.sh
# Requires pyenv with Python 3.11 installed.

set -e

PYENV_ROOT="${HOME}/.pyenv"
PYTHON311="${PYENV_ROOT}/versions/3.11.12/bin/python3.11"
VENV_DIR="${HOME}/.robot/venvs/xtts311"

echo "=== XTTS v2 venv setup ==="

if [[ ! -x "${PYTHON311}" ]]; then
  echo "Python 3.11 not found at ${PYTHON311}"
  echo "Run: export PYENV_ROOT=\$HOME/.pyenv && export PATH=\$PYENV_ROOT/bin:\$PATH && pyenv install 3.11.12"
  exit 1
fi

echo "[1/4] Creating venv at ${VENV_DIR}..."
"${PYTHON311}" -m venv "${VENV_DIR}"

echo "[2/4] Installing torch 2.4.0 CPU + torchaudio..."
"${VENV_DIR}/bin/pip" install --quiet \
  "torch==2.4.0+cpu" \
  "torchaudio==2.4.0+cpu" \
  --index-url https://download.pytorch.org/whl/cpu

echo "[3/4] Installing coqui-tts..."
"${VENV_DIR}/bin/pip" install --quiet coqui-tts

echo "[4/4] Smoke test..."
"${VENV_DIR}/bin/python3" -c "from TTS.api import TTS; print('OK')"

echo ""
echo "=== Done! Voice cloning is ready. ==="
echo "Enrol a voice:  python3 tools/enroll_voice.py --name 'YourName'"
echo "Test synthesis: python3 tools/enroll_voice.py --name 'YourName' --play"
