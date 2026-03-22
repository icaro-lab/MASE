#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: bash scripts/scaffold_environment.sh <environment-id> [Display Name]" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template_dir="$repo_root/environments/_template"
environment_id="$1"
display_name="${2:-}"
target_dir="$repo_root/environments/$environment_id"

if [[ ! "$environment_id" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "environment id must be kebab-case: $environment_id" >&2
  exit 1
fi

if [[ ! -d "$template_dir" ]]; then
  echo "template directory missing: $template_dir" >&2
  exit 1
fi

if [[ -e "$target_dir" ]]; then
  echo "target already exists: $target_dir" >&2
  exit 1
fi

if [[ -z "$display_name" ]]; then
  display_name="$(python3 - "$environment_id" <<'PY'
import sys
slug = sys.argv[1]
print(" ".join(part.capitalize() for part in slug.split("-")))
PY
)"
fi

cp -R "$template_dir" "$target_dir"

python3 - "$target_dir" "$environment_id" "$display_name" <<'PY'
from pathlib import Path
import sys

target = Path(sys.argv[1])
environment_id = sys.argv[2]
display_name = sys.argv[3]

for path in target.rglob("*"):
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8")
    text = text.replace("template-env", environment_id)
    text = text.replace("Template Environment", display_name)
    text = text.replace("mase-template-env", f"mase-{environment_id}")
    text = text.replace("mase-template-env-frontend", f"mase-{environment_id}-frontend")
    path.write_text(text, encoding="utf-8")
PY

echo "Created $target_dir"
echo "Next steps:"
echo "  1. Edit environments/$environment_id/environment.yaml"
echo "  2. Implement environments/$environment_id/backend/app/main.py"
echo "  3. Add or adjust skills under environments/$environment_id/skills/"
echo "  4. Update populations under environments/$environment_id/populations/"
echo "  5. Validate with POST /api/v1/environments/$environment_id/validate"
