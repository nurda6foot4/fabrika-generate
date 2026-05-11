#!/usr/bin/env python3
"""
run_videos.py — Resume video generation for a partially-completed job.
Usage: python3 run_videos.py <job_id> <html_file> [--scenario N] [--ceiling CREDITS]
"""
import argparse, sys
from pathlib import Path

import db, pipeline, postprocess
from main import parse_fabrika_html
from schema import ClipStatus

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("html_file")
    parser.add_argument("--scenario", type=int, required=True)
    parser.add_argument("--ceiling", type=float, default=300.0)
    args = parser.parse_args()

    pipeline.BUDGET_CEILING = args.ceiling
    db.init_db()

    html_path = Path(args.html_file)
    product, market, scenarios = parse_fabrika_html(html_path)
    scenario = next((s for s in scenarios if s.rank == args.scenario), None)
    if not scenario:
        sys.exit(f"Scenario rank {args.scenario} not found")

    clips_rows = db.get_all_clips(args.job_id)
    approved = [r for r in clips_rows if r["status"] == "keyframe_approved"]
    print(f"Job {args.job_id}: {len(approved)} clips to generate videos for")

    for row in sorted(approved, key=lambda r: r["clip_index"]):
        clip_id = row["clip_id"]
        idx = row["clip_index"]
        kf_job_id = row["keyframe_job_id"]
        scene = scenario.scenes[idx]

        print(f"\n── Video {idx+1}/{len(scenario.scenes)}: [{scene.label.value}] {scene.time_range} ({scene.duration_s}s)")
        try:
            raw_path = pipeline.run_video(
                args.job_id, clip_id, scenario, scene, kf_job_id,
            )
            dest = pipeline.OUTPUT_DIR / args.job_id / f"{scenario.scenario_id}_{idx:03d}_01_done.mp4"
            print(f"  Encoding for Premiere → {dest.name}")
            encoded = postprocess.encode_for_premiere(raw_path, dest, scenario.scenario_id, idx)
            info = postprocess.validate_output(encoded)
            print(f"  Validated: {info['width']}x{info['height']} {info['codec']} {info['duration_s']}s")

            clip = db.get_clip(clip_id)
            sidecar_data = {
                "clip_id": clip_id,
                "job_id": args.job_id,
                "scenario_id": scenario.scenario_id,
                "clip_index": idx,
                "scene_label": scene.label.value,
                "duration_s": scene.duration_s,
                "keyframe_job_id": kf_job_id,
                "video_job_id": clip.get("video_job_id"),
                "credits_keyframe": clip.get("credits_keyframe"),
                "credits_video": clip.get("credits_video"),
                "ffprobe": info,
            }
            sidecar = postprocess.write_sidecar(encoded, sidecar_data)
            db.update_clip(clip_id, status=ClipStatus.DONE.value, file_path=str(encoded))
            print(f"  Done: {encoded.name}")

        except Exception as e:
            print(f"  ❌ {e}")
            db.update_clip(clip_id, status=ClipStatus.FAILED.value)

    total = db.get_job_total_cost(args.job_id)
    print(f"\n✅ Done — total credits used this job: {total:.1f}")

if __name__ == "__main__":
    main()
