#!/usr/bin/env bash
#
# Reproduces the proof that the code prbot approved at 100/100 would have
# destroyed a live IAM Access Analyzer.
#
# Runs two plans against the same state file, one per resource shape:
#
#   original.tf  (commit eaa3480b, prbot: APPROVE 100/100)
#     expected:  "must be replaced" / "Plan: 1 to add, 0 to change, 1 to destroy."
#
#   merged.tf    (commit 9b67058c, after review findings fixed)
#     expected:  "No changes."
#
# No AWS credentials and no network are needed if a local provider mirror is
# available (see PROVIDER_MIRROR below). Otherwise tofu downloads
# terraform-provider-aws 5.100.0 from the registry, which needs network but
# still no credentials.
#
# Usage:  ./run.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOFU="${TOFU:-tofu}"

# Optional offline mirror. Point at any directory laid out as
#   <mirror>/registry.opentofu.org/hashicorp/aws/5.100.0/<os>_<arch>/
# An initialised infrastructure-core management layer already has one at
#   layers/management/.terraform/providers
PROVIDER_MIRROR="${PROVIDER_MIRROR:-}"

if ! command -v "$TOFU" >/dev/null 2>&1; then
  echo "error: '$TOFU' not on PATH. Set TOFU=/path/to/tofu." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp "$HERE/live-state.tfstate" "$WORK/terraform.tfstate"

if [ -n "$PROVIDER_MIRROR" ]; then
  if [ ! -d "$PROVIDER_MIRROR" ]; then
    echo "error: PROVIDER_MIRROR '$PROVIDER_MIRROR' is not a directory" >&2
    exit 1
  fi
  cat > "$WORK/.terraformrc" <<EOF
provider_installation {
  filesystem_mirror {
    path    = "$PROVIDER_MIRROR"
    include = ["registry.opentofu.org/hashicorp/aws"]
  }
  direct { exclude = ["registry.opentofu.org/hashicorp/aws"] }
}
EOF
  export TF_CLI_CONFIG_FILE="$WORK/.terraformrc"
  echo "Using offline provider mirror: $PROVIDER_MIRROR"
else
  echo "No PROVIDER_MIRROR set; tofu will fetch aws 5.100.0 from the registry."
fi

run_case() {
  local label="$1" src="$2" expect="$3"

  rm -f "$WORK"/*.tf
  cp "$HERE/$src" "$WORK/main.tf"
  # State must be restored each time: a plan does not mutate it, but this keeps
  # the two cases independent if the script is edited later.
  cp "$HERE/live-state.tfstate" "$WORK/terraform.tfstate"

  echo
  echo "============================================================"
  echo "CASE: $label   ($src)"
  echo "EXPECT: $expect"
  echo "============================================================"

  ( cd "$WORK" && "$TOFU" init -backend=false -input=false >/dev/null )
  ( cd "$WORK" && "$TOFU" plan -refresh=false -lock=false -input=false -no-color )
}

run_case "ORIGINAL (prbot approved this at 100/100)" \
         "original.tf" \
         "must be replaced -> Plan: 1 to add, 0 to change, 1 to destroy."

run_case "MERGED (after review findings were fixed)" \
         "merged.tf" \
         "No changes."

echo
echo "============================================================"
echo "Both plans above are the evidence. The only difference between"
echo "the two configurations is the presence of the configuration block."
echo "============================================================"
