#!/bin/bash
# usage: run_fable_demo.sh : Fable 5.2 demo (first 3 official cases) of RBench, PhyGenBench, PCVE
set -u
. "$(dirname "$(readlink -f "$0")")/env.sh"
for b in rbench phygenbench pcve; do
  echo "$(date -u +%FT%TZ) start $b fable-demo"
  pixelworld matrix run --config "$PIXELWORLD_CONFIGS/${b}_demo_fable.json" --interval 15
  echo "$(date -u +%FT%TZ) end $b rc=$?"
done
