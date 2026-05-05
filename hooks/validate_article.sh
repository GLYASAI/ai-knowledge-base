#!/usr/bin/env bash
set -euo pipefail

# Extract file_path from CLAUDE_TOOL_INPUT JSON
FILE_PATH=$(echo "${CLAUDE_TOOL_INPUT:-}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('file_path') or data.get('filePath') or '')
")

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Only validate knowledge/articles/*.json
case "$FILE_PATH" in
  knowledge/articles/*.json | */knowledge/articles/*.json) ;;
  *) exit 0 ;;
esac

exec python3 hooks/validate_json.py "$FILE_PATH"
