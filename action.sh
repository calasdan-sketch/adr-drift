#!/usr/bin/env bash
# The GitHub Action's step (action.yml runs this file with bash).
#
# The `args` input arrives in ADR_DRIFT_ARGS through the environment - never
# pasted into the script, so it cannot inject shell code. `read -a` splits
# it on whitespace (newlines too) into an array, with no filename expansion:
# an unquoted $ADR_DRIFT_ARGS would turn a `*` into the files of the working
# directory. There is no quoting, so a path with a space cannot be passed.
set -euo pipefail
IFS=$' \t\n' read -r -d '' -a adr_drift_args <<< "${ADR_DRIFT_ARGS:-}" || true
python -m pip install --quiet "$GITHUB_ACTION_PATH"
# ${a[@]+"${a[@]}"}: an empty array under `set -u` on bash 3.2 (macOS)
exec python -m adr_drift check ${adr_drift_args[@]+"${adr_drift_args[@]}"}
