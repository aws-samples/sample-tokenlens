#!/bin/sh
set -eu

PYTHON="${PYTHON:-python3}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CONSTRAINTS="$ROOT/constraints-release.txt"
cd "$ROOT"
VERSION=$(
  "$PYTHON" -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])'
)
DIST_NAME=$(
  "$PYTHON" -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["name"])'
)
ARTIFACT_NAME=$(
  "$PYTHON" -c 'import re, sys; print(re.sub(r"[-_.]+", "_", sys.argv[1]))' "$DIST_NAME"
)

if ! grep -F "## [$VERSION]" CHANGELOG.md >/dev/null 2>&1; then
  echo "CHANGELOG.md has no section for version $VERSION" >&2
  exit 1
fi

"$PYTHON" -m pytest -q
"$PYTHON" power/scripts/validate_power.py
"$PYTHON" skill/scripts/validate_skill.py

mkdir -p dist
rm -f dist/*.whl dist/*.tar.gz dist/install-kiro.sh \
  dist/constraints-release.txt dist/SHA256SUMS
PIP_CONSTRAINT="$CONSTRAINTS" "$PYTHON" -m build
cp scripts/install-kiro.sh dist/install-kiro.sh
cp constraints-release.txt dist/constraints-release.txt
(
  cd dist
  shasum -a 256 \
    "${ARTIFACT_NAME}-${VERSION}-py3-none-any.whl" \
    "${ARTIFACT_NAME}-${VERSION}.tar.gz" \
    install-kiro.sh \
    constraints-release.txt > SHA256SUMS
)

echo "Release artifacts ready in $ROOT/dist"
