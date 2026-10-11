#!/usr/bin/env bash
# check_marker.sh: confirm marker_single + llama-server are alive and working
# Usage: ./check_marker.sh [output_dir] [interval_seconds]
#        sudo ./check_marker.sh ...   (adds GPU sampling via powermetrics)

OUT_DIR="${1:-./out}"
INTERVAL="${2:-5}"

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

# CPU time of a PID in seconds (ps prints [[dd-]hh:]mm:ss.ss)
cpu_seconds() {
  ps -o time= -p "$1" 2>/dev/null | awk '{
    n = split($1, a, "[-:]"); s = 0
    for (i = 1; i <= n; i++) s = s * 60 + a[i]
    print s }'
}

swapins() { vm_stat | awk '/Swapins/ {gsub("\\.", "", $2); print $2}'; }

echo "=== marker status $(date '+%H:%M:%S') ==="

# 1. Processes
MARKER_PID=$(pgrep -f "marker_single|convert_book.py" | head -1)
LLAMA_PID=$(pgrep -x llama-server | head -1)

if [[ -z "$MARKER_PID" ]]; then
  red "marker_single: NOT running"
  exit 1
fi
green "marker_single: running (PID $MARKER_PID)"

if [[ -z "$LLAMA_PID" ]]; then
  yellow "llama-server: not running (normal while loading, or with --disable_ocr / clean digital pages)"
else
  green "llama-server: running (PID $LLAMA_PID)"
  PORT=$(lsof -nP -a -p "$LLAMA_PID" -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {split($9, a, ":"); print a[2]; exit}')
  if [[ -n "$PORT" ]]; then
    CONNS=$(lsof -nP -a -p "$MARKER_PID" -iTCP 2>/dev/null | grep -c "127.0.0.1:${PORT} (ESTABLISHED)")
    echo "  listening on 127.0.0.1:$PORT, open requests from marker: $CONNS"
  fi
fi

# 2. Activity over the interval
M_CPU0=$(cpu_seconds "$MARKER_PID")
[[ -n "$LLAMA_PID" ]] && L_CPU0=$(cpu_seconds "$LLAMA_PID")
SWAP0=$(swapins)
FILES0=$(find "$OUT_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')

echo "sampling for ${INTERVAL}s..."

GPU_ACTIVE=""
if [[ $EUID -eq 0 ]]; then
  GPU_ACTIVE=$(powermetrics --samplers gpu_power -i $((INTERVAL * 1000)) -n 1 2>/dev/null \
    | awk '/GPU HW active residency/ {print $5; exit}')
else
  sleep "$INTERVAL"
fi

M_CPU1=$(cpu_seconds "$MARKER_PID")
[[ -n "$LLAMA_PID" ]] && L_CPU1=$(cpu_seconds "$LLAMA_PID")
SWAP1=$(swapins)
FILES1=$(find "$OUT_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')

M_DELTA=$(awk -v a="$M_CPU0" -v b="$M_CPU1" 'BEGIN {printf "%.2f", b - a}')
echo "marker CPU time +${M_DELTA}s"

WORKING=0
if [[ -n "$LLAMA_PID" ]]; then
  L_DELTA=$(awk -v a="$L_CPU0" -v b="$L_CPU1" 'BEGIN {printf "%.2f", b - a}')
  echo "llama-server CPU time +${L_DELTA}s"
  awk -v d="$L_DELTA" 'BEGIN {exit !(d > 0.1)}' && WORKING=1
fi
awk -v d="$M_DELTA" 'BEGIN {exit !(d > 0.1)}' && WORKING=1

if [[ -n "$GPU_ACTIVE" ]]; then
  echo "GPU active residency: $GPU_ACTIVE"
  awk -v g="${GPU_ACTIVE%\%}" 'BEGIN {exit !(g > 10)}' && WORKING=1
else
  echo "GPU: not sampled (run with sudo to include)"
fi

echo "swap-ins during sample: $((SWAP1 - SWAP0))"
echo "files in $OUT_DIR: $FILES0 -> $FILES1"

# 3. Verdict
echo "---"
if [[ $WORKING -eq 1 ]]; then
  green "VERDICT: working"
else
  red "VERDICT: no CPU/GPU activity detected; possibly stalled (re-run to confirm)"
fi
if (( SWAP1 - SWAP0 > 500 )); then
  yellow "heavy swapping; consider SURYA_INFERENCE_PARALLEL=1 or 2"
fi
