#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8000
PANEL="$SCRIPT_DIR/server_panel.py"

lsof -ti :$PORT | xargs kill -9 2>/dev/null

if [ -f "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON_EXE="$SCRIPT_DIR/.venv/bin/python3"
elif [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    PYTHON_EXE="$SCRIPT_DIR/venv/bin/python3"
else
    PYTHON_EXE="python3"
fi

cd "$SCRIPT_DIR"
"$PYTHON_EXE" "$PANEL" &

exit 0
