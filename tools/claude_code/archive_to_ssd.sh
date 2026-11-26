#!/bin/bash
# usage: archive_to_ssd.sh <src_dir> <dest_tar>  : tar -> verify listing+size -> remove src
# e.g. archive_to_ssd.sh "$PIXELWORLD_HOME/archive/x" "$PIXELWORLD_OFFLOAD_DIR/archive/x.tar"
set -euo pipefail
src="$1"; dst="$2"; mkdir -p "$(dirname "$dst")"
tar -C "$(dirname "$src")" -cf "$dst.part" "$(basename "$src")"
n_src=$(find "$src" | wc -l); n_tar=$(tar -tf "$dst.part" | wc -l)
[ "$n_src" -eq "$n_tar" ] || { echo "MISMATCH $src $n_src vs $n_tar"; exit 1; }
tar -C "$(dirname "$src")" -df "$dst.part" >/dev/null   # content compare
sha256sum "$dst.part" | awk '{print $1}' > "$dst.sha256"
mv "$dst.part" "$dst"; rm -rf "$src"; echo "OK $src -> $dst"
