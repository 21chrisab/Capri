#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
    local cand
    for cand in python3.12 python3.13 python3.11 \
                /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11 \
                /usr/local/bin/python3.12 /usr/local/bin/python3.13 /usr/local/bin/python3.11 \
                python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
                command -v "$cand"
                return 0
            fi
        fi
    done
    return 1
}

VENV="${CAPRI_VENV:-$HOME/.capri/venv}"

if [[ ! -x "$VENV/bin/python" ]]; then
    PY="$(find_python || true)"
    if [[ -z "$PY" ]]; then
        echo "No Python 3.11-3.13 found. Install with: brew install python@3.12" >&2
        exit 1
    fi
    echo "Using: $PY"
    mkdir -p "$(dirname "$VENV")"
    "$PY" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt
"$VENV/bin/pip" install --no-deps "silero-vad>=5"

MODEL_DIR="$HOME/.capri/models"
mkdir -p "$MODEL_DIR"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
    if [[ ! -f "$MODEL_DIR/$f" ]]; then
        echo "Downloading $f ..."
        curl -L -f -o "$MODEL_DIR/$f" "$BASE_URL/$f"
    fi
done

if [[ ! -f config.local.toml ]]; then
    cat > config.local.toml <<'EOF'
[voice]
model_dir = "~/.capri/models"
EOF
fi

# Gemini key lives outside this (Google Drive) folder.
if [[ ! -f "$HOME/.capri/.env" ]]; then
    printf 'GEMINI_API_KEY=\n' > "$HOME/.capri/.env"
    chmod 600 "$HOME/.capri/.env"
    echo "Paste your Gemini key into ~/.capri/.env (open -e ~/.capri/.env)"
fi

echo
./capri.sh doctor
