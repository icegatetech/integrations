#!/usr/bin/env bash
# Checks every prerequisite a recipe needs, and prints the exact fix for each.
set -uo pipefail

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
ICEGATE_FLIGHTSQL_URI="${ICEGATE_FLIGHTSQL_URI:-grpc://localhost:8815}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:12b-mlx}"

fail=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n     fix: %s\n' "$1" "$2"; fail=1; }

port_open() {
  python3 - "$1" "$2" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(2)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
except Exception:
    sys.exit(1)
finally:
    s.close()
PY
}

echo "Checking prerequisites..."

if curl -sf -m 5 "${OLLAMA_BASE_URL}/api/tags" >/dev/null 2>&1; then
  ok "Ollama reachable at ${OLLAMA_BASE_URL}"
  if curl -sf -m 5 "${OLLAMA_BASE_URL}/api/tags" | grep -q "${OLLAMA_MODEL}"; then
    ok "Model ${OLLAMA_MODEL} present"
  else
    bad "Model ${OLLAMA_MODEL} not pulled" "ollama pull ${OLLAMA_MODEL}"
  fi
else
  bad "Ollama unreachable at ${OLLAMA_BASE_URL}" "start Ollama, then: ollama pull ${OLLAMA_MODEL}"
fi

otlp_host="$(echo "${OTEL_EXPORTER_OTLP_ENDPOINT}" | sed -E 's#^[a-z]+://##; s#:.*##')"
otlp_port="$(echo "${OTEL_EXPORTER_OTLP_ENDPOINT}" | sed -E 's#.*:([0-9]+).*#\1#')"
if port_open "${otlp_host}" "${otlp_port}"; then
  ok "IceGate OTLP gRPC reachable at ${OTEL_EXPORTER_OTLP_ENDPOINT}"
else
  bad "IceGate OTLP gRPC unreachable at ${OTEL_EXPORTER_OTLP_ENDPOINT}" \
      "cd ../icegate && make run-docker-core-release"
fi

fs_host="$(echo "${ICEGATE_FLIGHTSQL_URI}" | sed -E 's#^[a-z]+://##; s#:.*##')"
fs_port="$(echo "${ICEGATE_FLIGHTSQL_URI}" | sed -E 's#.*:([0-9]+).*#\1#')"
if port_open "${fs_host}" "${fs_port}"; then
  ok "IceGate Flight SQL reachable at ${ICEGATE_FLIGHTSQL_URI}"
else
  bad "IceGate Flight SQL unreachable at ${ICEGATE_FLIGHTSQL_URI}" \
      "cd ../icegate && make run-docker-core-release"
fi

if [ "${fail}" -ne 0 ]; then
  echo
  echo "Prerequisites missing. Recipes will appear to succeed while emitting nothing."
  exit 1
fi
echo
echo "All prerequisites satisfied."
