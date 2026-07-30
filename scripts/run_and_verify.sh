#!/usr/bin/env bash
# Runs a recipe, captures its ICEGATE_RUN_ID, then asserts its telemetry.
set -euo pipefail

RECIPE="${1:?usage: run_and_verify.sh <recipe-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}/${RECIPE}"

echo "==> running ${RECIPE}"
if [ -f pyproject.toml ]; then
  output="$(uv run python -m recipe 2>&1 | tee /dev/stderr)"
elif [ -f package.json ]; then
  output="$(npm start --silent 2>&1 | tee /dev/stderr)"
else
  echo "unrecognised recipe type in ${RECIPE}" >&2
  exit 2
fi

run_id="$(echo "${output}" | sed -n 's/^ICEGATE_RUN_ID=//p' | tail -1)"
if [ -z "${run_id}" ]; then
  echo "recipe did not print ICEGATE_RUN_ID=<hex> (see CONVENTIONS.md)" >&2
  exit 1
fi

echo "==> verifying run ${run_id}"
cd "${ROOT}/verify"
uv run python -m assert_runner "${ROOT}/${RECIPE}/expectations.yaml" "${run_id}"
