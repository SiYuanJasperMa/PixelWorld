#!/bin/bash
# usage: run_main.sh <opus|fable> : run benches sequentially (Physics-IQ -> RBench -> PhyGenBench -> PCVE)
# NOTE: when one matrix ends this script starts the next bench. To stop, kill THIS bash
# process first, then create the STOP file in the active runs_root (see the runbook).
set -u
. "$(dirname "$(readlink -f "$0")")/env.sh"
if [ "$1" = opus ]; then B="${OPUS_BENCHES:-physics_iq rbench phygenbench pcve}"; else B="physics_iq rbench"; fi
for b in $B; do
  echo "$(date -u +%FT%TZ) start $b $1"
  pixelworld matrix run --config "$PIXELWORLD_CONFIGS/${b}_main_$1.json" --interval 15
  echo "$(date -u +%FT%TZ) end $b $1 rc=$?"
done
