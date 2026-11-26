#!/bin/bash
# usage: run_smoke.sh <bench>   (physics_iq|rbench|phygenbench|pcve)
set -u
. "$(dirname "$(readlink -f "$0")")/env.sh"
exec pixelworld matrix run --config "$PIXELWORLD_CONFIGS/$1_smoke5.json"
