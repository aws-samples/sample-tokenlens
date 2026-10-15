#!/bin/sh
set -eu

TOKENLENS_VERSION="${TOKENLENS_VERSION:-0.2.0}"
WORKSPACE="${1:-$PWD}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$SCRIPT_DIR/constraints-release.txt" ]; then
  CONSTRAINTS="$SCRIPT_DIR/constraints-release.txt"
elif [ -f "$SCRIPT_DIR/../constraints-release.txt" ]; then
  CONSTRAINTS="$SCRIPT_DIR/../constraints-release.txt"
else
  echo "Missing constraints-release.txt beside installer or repository root." >&2
  exit 1
fi

# Prefer the release artifact shipped beside this script. TokenLens is not
# published to a package registry, so a registry install fails on a new machine
# with "aws-samples-tokenlens was not found in the package registry". Falling
# back to the registry keeps the script working once it is published.
WHEEL=$(ls "$SCRIPT_DIR"/aws_samples_tokenlens-"$TOKENLENS_VERSION"-*.whl 2>/dev/null | head -1 || true)
if [ -z "$WHEEL" ]; then
  WHEEL=$(ls "$SCRIPT_DIR"/../dist/aws_samples_tokenlens-"$TOKENLENS_VERSION"-*.whl 2>/dev/null | head -1 || true)
fi
if [ -n "$WHEEL" ]; then
  echo "Installing TokenLens from local artifact: $WHEEL"
  SOURCE="$WHEEL"
  SPEC="$WHEEL"
else
  echo "No local wheel found; installing aws-samples-tokenlens from registry."
  SOURCE="aws-samples-tokenlens==${TOKENLENS_VERSION}"
  SPEC="aws-samples-tokenlens[mcp,tokenizers]==${TOKENLENS_VERSION}"
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force --constraints "$CONSTRAINTS" \
    --with "mcp>=1.2,<2" --with "tiktoken>=0.6" \
    "$SOURCE"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force --pip-args="--constraint $CONSTRAINTS" "$SPEC"
else
  echo "TokenLens requires uv or pipx. Install one, then rerun this script." >&2
  exit 1
fi

if ! command -v tokenlens >/dev/null 2>&1; then
  echo "TokenLens installed but 'tokenlens' is not on PATH." >&2
  echo "Add the installer's bin directory (for example ~/.local/bin) to PATH." >&2
  exit 1
fi

case "$(tokenlens --version)" in
  *" ${TOKENLENS_VERSION}") ;;
  *)
    echo "Installed TokenLens version does not match ${TOKENLENS_VERSION}." >&2
    exit 1
    ;;
esac

# Fail before touching the workspace if the MCP server cannot start. The SDK
# ships a 2.x line that removed the server API TokenLens uses, so a successful
# package install does not by itself mean the server runs.
if ! tokenlens mcp --probe; then
  echo "TokenLens installed but its MCP server failed to start." >&2
  echo "Reinstall with a supported MCP SDK (mcp>=1.2,<2) and rerun." >&2
  exit 1
fi

tokenlens kiro install --workspace "$WORKSPACE"
tokenlens kiro doctor --workspace "$WORKSPACE"
