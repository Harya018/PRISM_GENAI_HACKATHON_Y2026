#!/usr/bin/env bash
# One-command FDB-v3 reproduction: install -> configure -> download data -> run our agent
# -> evaluate -> write scores/logs/config/seeds to results/.
#
#   ./scripts/run_fdb_v3.sh              # our agent (default: LK_PROVIDER=gemini2_5)
#   ./scripts/run_fdb_v3.sh --baseline   # the FDB-v3 stock template, unmodified — for comparison
#   LK_PROVIDER=gpt_realtime ./scripts/run_fdb_v3.sh
#
# Requires .env in the repo root (copy .env.example, fill in LIVEKIT_* + a provider key).
# Target: Linux, single 48GB NVIDIA GPU, CUDA 12.x/13.x (the organizers' own re-run machine per
# docs/Theme05_Participant_Guide_UPDATED_FBD.docx) or a declared hosted API — the agent itself
# needs no local GPU (realtime models are hosted APIs); only the benchmark's own NeMo ASR
# verification step benefits from one (falls back to CPU otherwise, just slower).
set -euo pipefail

REPRO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FDB_COMMIT="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"   # pinned — docs/RESEARCH_FDB.md ss1
FDB_DATA_GDRIVE_ID="1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz"
PROVIDER="${LK_PROVIDER:-gemini2_5}"
MODE="ours"
SEED=1234

for arg in "$@"; do
  case "$arg" in
    --baseline) MODE="baseline" ;;
    --provider=*) PROVIDER="${arg#*=}" ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${MODE}_${PROVIDER}"
RESULTS_DIR="${REPRO_ROOT}/results/${MODE}/${RUN_ID}"
mkdir -p "${RESULTS_DIR}"

if [ ! -f "${REPRO_ROOT}/.env" ]; then
  echo "Missing .env — copy .env.example, fill in LIVEKIT_URL/API_KEY/API_SECRET and a" >&2
  echo "provider key (GOOGLE_API_KEY for the default gemini2_5 provider), then re-run." >&2
  exit 1
fi
set -a; source "${REPRO_ROOT}/.env"; set +a

export PYTHONHASHSEED="${SEED}"
export LK_PROVIDER="${PROVIDER}"

echo "== [1/6] Clone FDB-v3 (pinned commit ${FDB_COMMIT}) =="
FDB_DIR="${REPRO_ROOT}/.fdb-v3"
if [ ! -d "${FDB_DIR}" ]; then
  git clone https://github.com/DanielLin94144/Full-Duplex-Bench.git "${FDB_DIR}"
fi
git -C "${FDB_DIR}" fetch --depth=1 origin "${FDB_COMMIT}" 2>/dev/null || true
git -C "${FDB_DIR}" checkout "${FDB_COMMIT}"
echo "$(git -C "${FDB_DIR}" rev-parse HEAD)" > "${RESULTS_DIR}/fdb_commit.txt"

echo "== [2/6] Python env + dependencies =="
VENV="${REPRO_ROOT}/.venv"
if [ ! -d "${VENV}" ]; then
  python3 -m venv "${VENV}"
fi
source "${VENV}/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "${REPRO_ROOT}/requirements.txt"
pip freeze > "${RESULTS_DIR}/pip_freeze.txt"

echo "== [3/6] Benchmark data =="
DATA_DIR="${FDB_DIR}/v3/fdb_v3_data_released"
if [ ! -d "${DATA_DIR}" ]; then
  python3 -c "import gdown; gdown.download(id='${FDB_DATA_GDRIVE_ID}', output='${FDB_DIR}/v3/fdb_v3_data_released.zip', quiet=False)"
  python3 -c "import zipfile; zipfile.ZipFile('${FDB_DIR}/v3/fdb_v3_data_released.zip').extractall('${FDB_DIR}/v3')"
fi

echo "== [4/6] Start the agent =="
if [ "${MODE}" = "baseline" ]; then
  AGENT_SCRIPT="lk_agent_tool.py"          # FDB-v3's own stock template, unmodified
else
  cp "${REPRO_ROOT}/agent/lk_agent.py" "${FDB_DIR}/v3/lk_agent_ours.py"
  cp "${REPRO_ROOT}/agent/guard.py" "${FDB_DIR}/v3/guard.py"
  cp "${REPRO_ROOT}/agent/instructions.py" "${FDB_DIR}/v3/instructions.py"
  AGENT_SCRIPT="lk_agent_ours.py"
fi

pushd "${FDB_DIR}/v3" > /dev/null
python3 "${AGENT_SCRIPT}" start > "${RESULTS_DIR}/agent_server.log" 2>&1 &
AGENT_PID=$!
echo "${AGENT_PID}" > "${RESULTS_DIR}/agent.pid"
trap 'kill "${AGENT_PID}" 2>/dev/null || true' EXIT
echo "Agent server started (pid ${AGENT_PID}), waiting for LiveKit Cloud registration..."
sleep 10

echo "== [5/6] Run inference against all 100 scenarios =="
python3 run_tool_benchmark_all_released.py --provider "${PROVIDER}" --force \
  2>&1 | tee "${RESULTS_DIR}/inference.log"

kill "${AGENT_PID}" 2>/dev/null || true
trap - EXIT

echo "== [6/6] Evaluate (LLM judge enabled — matches the official pinned-judge policy) =="
python3 evaluate_tool_calls.py --benchmark benchmark_data_v2.json \
  --results-dir fdb_v3_data_released --provider "${PROVIDER}" \
  --output "${RESULTS_DIR}/${PROVIDER}_evaluation_report.json" --use-llm
python3 evaluate_pass_rate.py --benchmark benchmark_data_v2.json \
  --results-dir fdb_v3_data_released --provider "${PROVIDER}" \
  --output "${RESULTS_DIR}/${PROVIDER}_pass_rate_report.json" --use-llm
python3 analyze_tool_latency.py --results-dir fdb_v3_data_released --provider "${PROVIDER}" \
  | tee "${RESULTS_DIR}/${PROVIDER}_latency_report.txt"
popd > /dev/null

cat > "${RESULTS_DIR}/run_config.json" <<EOF
{
  "mode": "${MODE}",
  "provider": "${PROVIDER}",
  "fdb_commit": "${FDB_COMMIT}",
  "seed": ${SEED},
  "run_id": "${RUN_ID}"
}
EOF

echo "Done. Results in ${RESULTS_DIR}"
