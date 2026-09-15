#!/usr/bin/env bash
# Run the prbot prompt evaluations.
#
# These make real Bedrock calls. A full run of the default suite is roughly
# 24 calls; at Sonnet pricing that is well under a dollar, but it is not free
# and it is not deterministic, so it is not part of `pytest`.
#
# Requires AWS credentials with bedrock:InvokeModel on the configured model.
set -euo pipefail

cd "$(dirname "$0")"

# promptfoo shells out to `python`; point it at the project venv so prbot and
# its dependencies are importable.
export PROMPTFOO_PYTHON="${PROMPTFOO_PYTHON:-$(cd .. && uv run python -c 'import sys; print(sys.executable)')}"
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-southeast-2}}"

CONFIG="${1:-promptfooconfig.yaml}"
shift || true

echo "config: $CONFIG"
echo "python: $PROMPTFOO_PYTHON"
echo "region: $AWS_REGION"
echo

exec npx --yes promptfoo@0.123.0 eval -c "$CONFIG" "$@"
