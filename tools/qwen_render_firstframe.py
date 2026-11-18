#!/usr/bin/env python3
"""Render an agent's pixel first frame into a photorealistic conditioning image.

This is an analysis-only renderer: it never generates video frames and never
participates in the PixelWorld main generation path. Qwen-Image-2.1 receives
the agent frame as a condition image plus the original scene prompt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch
from PIL import Image
from diffusers import QwenImage21Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--negative-prompt", default=None)
    parser.add_argument("--cfg", type=float, default=1.0)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, help="Explicit candidate seed; omitted seeds remain recorded as unknown")
    parser.add_argument("--revision-label", help="Owner-supplied model revision label; not a weight-hash verification")
    args = parser.parse_args()
    pipe = QwenImage21Pipeline.from_pretrained(
        str(args.model), torch_dtype=torch.bfloat16, local_files_only=True
    )
    pipe.to(args.device)
    source = Image.open(args.image).convert("RGB")
    generator={} if args.seed is None else {"generator":torch.Generator(device=args.device).manual_seed(args.seed)}
    result = pipe(
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        true_cfg_scale=args.cfg,
        image=source,
        height=args.height,
        width=args.width,
        num_inference_steps=args.steps,
        output_resolution=max(args.width, args.height),
        **generator,
    ).images[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.output)
    index=args.model/'model_index.json'
    metadata={'pipeline':type(pipe).__name__,'model_reference':args.model.name,
              'revision_label':args.revision_label,
              'model_index_sha256':hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None,
              'condition_image_sha256':hashlib.sha256(args.image.read_bytes()).hexdigest(),
              'output_image_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
              'source_size':list(source.size),'requested_size':[args.width,args.height],
              'native_output_size':list(result.size),'seed':args.seed,'steps':args.steps,
              'cfg':args.cfg,'prompt':args.prompt,'negative_prompt':args.negative_prompt,
              'scope':'analysis initial-image preparation only; image passed to pipeline; no video frames generated'}
    args.output.with_suffix('.render.json').write_text(json.dumps(metadata,indent=2))
    print({"output": str(args.output), "size": result.size, "mode": result.mode})


if __name__ == "__main__":
    main()
