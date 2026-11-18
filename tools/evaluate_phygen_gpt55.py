#!/usr/bin/env python3
"""Score PhyGenBench videos with a GPT-5.5 VLM judge.

This is a PixelWorld judge protocol, not the official GPT-4o-0513 PhyGenEval.
It samples frames from the agent's saved RGB frames, asks GPT-5.5 for a 1-5
physical-consistency score, and records the exact prompt, model, case and
frame indices. Scores stay outside git under runs/.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path
import re
import math
import threading
import time

import numpy as np
from PIL import Image
from openai import AzureOpenAI

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = ("gpt61-sol", "gpt56-sol", "gpt56-luna", "gpt56-terra")
CATEGORY_MAP = {
    "Force": "Mechanics",
    "Light": "Optics",
    "Heat": "Thermal",
    "Physical Properties": "Material",
    "Chemical Properties": "Material",
}
PROMPT = """You are a strict physical-commonsense judge for generated videos.

Prompt: {caption}
Physical law: {physical_law}
Category: {main_category} / {sub_category}

The {frames} images are uniformly sampled frames from a 5-second video, in chronological order.
Judge how faithfully the video depicts the prompt and follows the physical law.

Score:
5 = fully consistent, no visible violation
4 = minor or ambiguous violation
3 = partially correct but with a clear violation
2 = major violation of the physical law
1 = completely inconsistent or unrelated

Return only one JSON object:
{{"score": <integer 1-5>, "reason": "<at most 20 words>"}}
"""


def read(path: Path):
    return json.loads(path.read_text())


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def selected_run(folder: Path, case: str):
    candidates = []
    for run in folder.glob(f"case_phygen_{case}_attempt_*"):
        manifest = run / "run_manifest.json"
        if not manifest.is_file():
            continue
        record = read(manifest)
        validation = record.get("validation") or {}
        spec = record.get("media_contract") or {}
        if (record.get("status") == "complete" and validation.get("pass")
                and validation.get("frames") == 120 and spec.get("frames") == 120):
            candidates.append((int(record.get("attempt", run.name.rsplit("_", 1)[1])), run, record))
    if not candidates:
        raise FileNotFoundError(f"No accepted 120-frame delivery for {folder.name}/{case}")
    return min(candidates, key=lambda row: row[0])


def frame_images(run: Path, count: int, max_edge: int) -> tuple[list[str], list[int]]:
    frames = sorted((run / "workspace/frames").glob("*.png"))
    if len(frames) != 120:
        raise ValueError(f"Expected 120 frames, found {len(frames)} in {run}")
    indices = np.linspace(0, len(frames) - 1, count).astype(int).tolist()
    encoded = []
    for index in indices:
        with Image.open(frames[index]) as image:
            rgb = image.convert("RGB")
            scale = min(1.0, max_edge / max(rgb.size))
            if scale < 1.0:
                rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=85)
        encoded.append(base64.b64encode(buffer.getvalue()).decode())
    return encoded, indices


def video_images(video: Path, count: int, max_edge: int) -> tuple[list[str], list[int]]:
    """Sample the same 120-frame window as agent deliveries without exporting PNGs."""
    import cv2

    capture = cv2.VideoCapture(str(video))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not capture.isOpened() or not math.isclose(fps, 24, abs_tol=0.01) or total not in (120, 121):
            raise ValueError(f"Expected 24 FPS and 120/121 frames: {video.name}, fps={fps}, frames={total}")
        indices = np.linspace(0, 119, count).astype(int).tolist()
        wanted = set(indices)
        encoded = {}
        for index in range(120):
            ok, frame = capture.read()
            if not ok:
                raise ValueError(f"Undecodable frame {index} in {video.name}")
            if index not in wanted:
                continue
            rgb = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            scale = min(1.0, max_edge / max(rgb.size))
            if scale < 1.0:
                rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=85)
            encoded[index] = base64.b64encode(buffer.getvalue()).decode()
        return [encoded[index] for index in indices], indices
    finally:
        capture.release()


def parse_score(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"No JSON object in response: {text[:200]}")
    value = json.loads(match.group(0))
    score = value.get("score")
    if type(score) is not int or not 1 <= score <= 5:
        raise ValueError(f"Invalid score: {score!r}")
    return {"score": score, "reason": str(value.get("reason", ""))[:240]}


def score_case(client, key_file: Path, job: dict, output: Path, frames: int, max_edge: int) -> dict:
    model, case, run = job["model"], job["case"], Path(job["run"])
    images, indices = (video_images(run, frames, max_edge) if job.get("source_type") == "external_video"
                       else frame_images(run, frames, max_edge))
    prompt = PROMPT.format(caption=job["caption"], physical_law=job["physical_law"],
                           main_category=job["main_category"], sub_category=job["sub_category"],
                           frames=len(images))
    content = [{"type": "text", "text": prompt}]
    for image in images:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image}})
    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model="gpt-5.5-2026-04-24",
                messages=[{"role": "user", "content": content}],
                max_completion_tokens=8192,
            )
            parsed = parse_score(response.choices[0].message.content)
            return {
                "model": model, "case": case, "score": parsed["score"], "reason": parsed["reason"],
                "main_category": job["main_category"], "sub_category": job["sub_category"],
                "dimension": CATEGORY_MAP.get(job["main_category"], job["main_category"]),
                "caption": job["caption"], "physical_law": job["physical_law"],
                "attempt": job["attempt"], "run": str(run), "frame_indices": indices,
                "judge_model": "gpt-5.5-2026-04-24",
                "judge_protocol": "PixelWorld PhyGen GPT-5.5 judge; not official GPT-4o-0513 PhyGenEval",
                "response_id": getattr(response, "id", None),
                "source_type": job.get("source_type", "agent_saved_rgb"),
                "source_sha256": job.get("source_sha256"),
                "judge_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "judge_usage": response.usage.model_dump() if response.usage else None,
            }
        except Exception as error:
            last_error = error
            time.sleep(5 * attempt)
    raise RuntimeError(f"{model}/{case}: {type(last_error).__name__}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generations", type=Path,
                        default=ROOT / "runs/final_release_candidate_20261117/formal_generations/phygenbench")
    parser.add_argument("--prompts", type=Path,
                        default=ROOT / "vendor/phygenbench-official-source/PhyGenBench/prompts.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "runs/final_release_candidate_20261117/evaluation/phygenbench_gpt55_judge")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--video-dir", type=Path, help="External baseline MP4 directory; requires exactly one model label")
    parser.add_argument("--cases", nargs="+", default=None, help="Optional case numbers, e.g. 1 2")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--max-edge", type=int, default=768)
    parser.add_argument("--key-file", type=Path,
                        default=ROOT / "runs/final_release_candidate_20261117/state/gpt55.key")
    args = parser.parse_args()
    if args.workers < 1 or args.frames < 2 or args.max_edge < 128:
        raise ValueError("workers >= 1, frames >= 2, max_edge >= 128")
    prompts = read(args.prompts)
    if len(prompts) != 160:
        raise ValueError("Official PhyGenBench prompt count changed")
    cases = [f"{int(i):03d}" for i in (args.cases or range(1, 161))]
    jobs = []
    if args.video_dir and len(args.models) != 1:
        raise ValueError("External video input requires exactly one --models label")
    for model in args.models:
        folder = args.generations / model / "vision-agent"
        for case in cases:
            if args.video_dir:
                run = args.video_dir.resolve() / f"phygen_{case}.mp4"
                if not run.is_file():
                    raise FileNotFoundError(run)
                attempt = None
            else:
                attempt, run, _ = selected_run(folder, case)
            metadata = prompts[int(case) - 1]
            jobs.append({"model": model, "case": case, "run": str(run), "attempt": attempt,
                         "caption": metadata["caption"], "physical_law": metadata["physical_laws"],
                         "main_category": metadata["main_category"],
                         "sub_category": metadata["sub_category"]})
            if args.video_dir:
                jobs[-1].update(source_type="external_video", source_sha256=hashlib.sha256(run.read_bytes()).hexdigest())
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output / "per_case.jsonl"
    done = set()
    if checkpoint.is_file():
        for line in checkpoint.read_text().splitlines():
            try:
                row = json.loads(line)
                done.add((row["model"], row["case"]))
            except (ValueError, KeyError):
                continue
    pending = [job for job in jobs if (job["model"], job["case"]) not in done]
    print(json.dumps({"jobs": len(jobs), "already_done": len(done), "pending": len(pending)}), flush=True)
    key = args.key_file.read_text().strip()
    client = AzureOpenAI(api_key=key, api_version="2023-07-01-preview",
                         azure_endpoint="https://search.bytedance.net/gpt/openapi/online/multimodal/crawl",
                         timeout=180)
    lock = threading.Lock()
    failures = []
    with checkpoint.open("a") as stream, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(score_case, client, args.key_file, job, args.output, args.frames, args.max_edge): job
                   for job in pending}
        for future in as_completed(futures):
            job = futures[future]
            try:
                row = future.result()
            except Exception as error:
                failures.append({"model": job["model"], "case": job["case"], "error": str(error)[:500]})
                print(json.dumps(failures[-1]), flush=True)
                continue
            with lock:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                atomic_json(args.output / row["model"] / f"{row['case']}.json", row)
            print(json.dumps({"model": row["model"], "case": row["case"], "score": row["score"],
                              "dimension": row["dimension"]}), flush=True)
    rows = []
    if checkpoint.is_file():
        for line in checkpoint.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    summary = {"judge_model": "gpt-5.5-2026-04-24",
               "judge_protocol": "PixelWorld PhyGen GPT-5.5 judge; not official GPT-4o-0513 PhyGenEval",
               "frames_per_video": args.frames, "models": {}}
    for model in args.models:
        model_rows = [row for row in rows if row["model"] == model]
        by_dimension = {}
        for row in model_rows:
            by_dimension.setdefault(row["dimension"], []).append(row["score"])
        dimensions = {name: {"cases": len(values), "mean_1_5": sum(values) / len(values),
                             "mean_0_1": (sum(values) / len(values) - 1) / 4}
                      for name, values in sorted(by_dimension.items())}
        all_scores = [row["score"] for row in model_rows]
        summary["models"][model] = {
            "cases": len(model_rows),
            "mean_1_5": sum(all_scores) / len(all_scores) if all_scores else None,
            "mean_0_1": (sum(all_scores) / len(all_scores) - 1) / 4 if all_scores else None,
            "dimensions": dimensions,
        }
    summary["failures"] = failures
    atomic_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:4000], flush=True)


if __name__ == "__main__":
    main()
