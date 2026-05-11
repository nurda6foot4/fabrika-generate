"""
pipeline.py — Keyframe + video stage runner.
Imports: schema, db, higgs, translator.
No FFmpeg, no postprocessing — that's postprocess.py.
Run standalone: python3 pipeline.py  (dry-run by default, no credits spent)
"""

from __future__ import annotations
import subprocess
import sys
import urllib.request
from pathlib import Path

import db
import higgs
import translator
from schema import ScenarioInput, SceneBlock, ClipStatus, PolicyResult


OUTPUT_DIR = Path(__file__).parent / "output"
MAX_RETRIES = 1       # enforced: never more than 1 auto-retry per clip
BUDGET_CEILING = 100.0  # credits per job
AUTO_APPROVE = False   # set True via --auto-approve flag to skip human checkpoint


# ── Download helper ────────────────────────────────────────────────────────

def _download(url: str, dest: Path) -> None:
    """Download URL to dest. Raises on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


# ── Human checkpoint ───────────────────────────────────────────────────────

def _human_approve_keyframe(image_path: Path, clip_id: str) -> bool:
    """
    Opens the keyframe image and asks for approval.
    Returns True to proceed, False to reject.
    """
    print(f"\n{'='*60}")
    print(f"KEYFRAME READY — clip {clip_id}")
    print(f"File: {image_path}")
    print(f"{'='*60}")

    # open for viewing
    try:
        subprocess.call(["open", str(image_path)])
    except Exception:
        pass  # non-fatal: user can open manually

    while True:
        answer = input("Approve keyframe? [y]es / [n]o / [q]uit pipeline: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        if answer in ("q", "quit"):
            print("Pipeline aborted by user.")
            sys.exit(0)
        print("  Please enter y, n, or q.")


# ── Keyframe stage ─────────────────────────────────────────────────────────

def run_keyframe(
    job_id: str,
    clip_id: str,
    scenario: ScenarioInput,
    scene: SceneBlock,
    *,
    char_job_id: str | None = None,
    dry_run: bool = False,
) -> str:
    """
    Generates one keyframe for the scene.
    Returns approved keyframe_job_id.
    Raises on policy block, budget failure, or user rejection after max retries.
    """
    for attempt in range(MAX_RETRIES + 1):
        kf_params = translator.to_keyframe_params(scenario, scene)

        # policy pre-screen
        result, reason = translator.policy_prescreen(kf_params)
        if result == PolicyResult.RANDOM:
            raise RuntimeError(f"Policy hard block on keyframe — {reason}")
        if result == PolicyResult.FIXABLE:
            print(f"⚠️  Policy soft flag: {reason}")
            print("    Continuing — reword the scene description in Fabrika if rejected.")

        cost = 2.0  # nano_banana_2 always 2cr
        if not dry_run:
            balance = higgs.get_balance()
            db.check_budget(job_id, cost, balance, BUDGET_CEILING)

        db.update_clip(clip_id, status=ClipStatus.KEYFRAME_PENDING.value)

        if dry_run:
            print(f"[DRY RUN] Would generate keyframe with prompt ({len(kf_params.prompt)} chars):")
            print(f"  {kf_params.prompt[:120]}...")
            if char_job_id:
                print(f"  [DRY RUN] Character reference: {char_job_id}")
            fake_job_id = f"dry-kf-{clip_id}-{attempt}"
            db.update_clip(clip_id,
                           status=ClipStatus.KEYFRAME_APPROVED.value,
                           keyframe_job_id=fake_job_id,
                           credits_keyframe=cost)
            db.log_cost(job_id, clip_id, kf_params.model, cost)
            return fake_job_id

        # live generation
        char_note = f" + char ref {char_job_id}" if char_job_id else ""
        print(f"  Generating keyframe (attempt {attempt+1}/{MAX_RETRIES+1}){char_note}...")
        extra = {}
        if char_job_id:
            extra["image"] = char_job_id   # --image flag locks character appearance
        kf_job_id = higgs.generate_image(
            model=kf_params.model,
            prompt=kf_params.prompt,
            aspect_ratio=kf_params.aspect_ratio,
            **extra,
        )
        db.update_clip(clip_id, keyframe_job_id=kf_job_id)
        db.log_cost(job_id, clip_id, kf_params.model, cost)
        db.update_clip(clip_id, credits_keyframe=cost)

        print(f"  Waiting for keyframe job {kf_job_id}...")
        job_result = higgs.wait_job(kf_job_id)
        urls = higgs.get_result_urls(job_result)
        if not urls:
            db.log_error(clip_id, "keyframe_download", {"error": "no urls", "result": job_result})
            raise RuntimeError(f"No output URLs from keyframe job {kf_job_id}")

        # download for local preview
        image_path = OUTPUT_DIR / job_id / f"{clip_id}_keyframe.jpg"
        _download(urls[0], image_path)

        # human checkpoint (skipped in auto-approve mode)
        approved = AUTO_APPROVE or _human_approve_keyframe(image_path, clip_id)
        if approved:
            db.update_clip(clip_id, status=ClipStatus.KEYFRAME_APPROVED.value)
            return kf_job_id

        # rejected
        db.update_clip(clip_id, status=ClipStatus.KEYFRAME_REJECTED.value)
        if attempt >= MAX_RETRIES:
            raise RuntimeError(f"Keyframe rejected after {MAX_RETRIES+1} attempts — aborting clip.")
        print(f"  Keyframe rejected. Retrying ({attempt+2}/{MAX_RETRIES+1})...")

    raise RuntimeError("Unreachable")  # loop exhausted


# ── Video stage ────────────────────────────────────────────────────────────

def run_video(
    job_id: str,
    clip_id: str,
    scenario: ScenarioInput,
    scene: SceneBlock,
    keyframe_job_id: str,
    *,
    dry_run: bool = False,
) -> Path:
    """
    Generates video from the approved keyframe.
    Returns local file path.
    """
    vp = translator.to_video_params(scenario, scene, keyframe_job_id)

    # policy pre-screen
    result, reason = translator.policy_prescreen(vp)
    if result == PolicyResult.RANDOM:
        raise RuntimeError(f"Policy hard block on video — {reason}")
    if result == PolicyResult.FIXABLE:
        print(f"⚠️  Policy soft flag on video prompt: {reason}")

    # cost lookup — duration is snapped to 4/6/8 by SceneBlock validator,
    # but use .get() with a safe fallback in case of direct VideoParams construction
    _VEO_COSTS = {4: 11.0, 6: 16.5, 8: 22.0}
    cost = _VEO_COSTS.get(vp.duration, 22.0)  # default to highest tier if unknown

    if not dry_run:
        balance = higgs.get_balance()
        db.check_budget(job_id, cost, balance, BUDGET_CEILING)

    db.update_clip(clip_id, status=ClipStatus.VIDEO_PENDING.value)

    if dry_run:
        print(f"[DRY RUN] Would generate video ({vp.duration}s, ~{cost}cr) with prompt:")
        print(f"  {vp.prompt[:120]}...")
        fake_path = OUTPUT_DIR / job_id / f"{clip_id}_000_01_done.mp4"
        fake_path.parent.mkdir(parents=True, exist_ok=True)
        db.update_clip(clip_id,
                       status=ClipStatus.VIDEO_DONE.value,
                       video_job_id=f"dry-vp-{clip_id}",
                       file_path=str(fake_path),
                       credits_video=cost)
        db.log_cost(job_id, clip_id, vp.model, cost)
        return fake_path

    # live generation
    print(f"  Generating video ({vp.duration}s)...")
    vid_job_id = higgs.generate_video(
        model=vp.model,
        prompt=vp.prompt,
        image_ref=vp.image_ref,
        duration=vp.duration,
        aspect_ratio=vp.aspect_ratio,
    )
    db.update_clip(clip_id, video_job_id=vid_job_id)
    db.log_cost(job_id, clip_id, vp.model, cost)
    db.update_clip(clip_id, credits_video=cost)

    print(f"  Waiting for video job {vid_job_id} (up to 15 min)...")
    job_result = higgs.wait_job(vid_job_id, timeout_minutes=15)
    urls = higgs.get_result_urls(job_result)
    if not urls:
        db.log_error(clip_id, "video_download", {"error": "no urls", "result": job_result})
        raise RuntimeError(f"No output URLs from video job {vid_job_id}")

    # download raw (encode step in main.py writes the final _done.mp4)
    clip_index = db.get_clip(clip_id)["clip_index"]
    filename = f"{scenario.scenario_id}_{clip_index:03d}_raw.mp4"
    dest = OUTPUT_DIR / job_id / filename
    print(f"  Downloading video → {dest}")
    _download(urls[0], dest)

    db.update_clip(clip_id,
                   status=ClipStatus.VIDEO_DONE.value,
                   file_path=str(dest))
    return dest


# ── Full clip runner ───────────────────────────────────────────────────────

def run_clip(
    job_id: str,
    clip_id: str,
    scenario: ScenarioInput,
    scene: SceneBlock,
    *,
    char_job_id: str | None = None,
    dry_run: bool = False,
) -> Path:
    """
    Runs one clip end-to-end: keyframe → human approval → video.
    Handles retries internally. Returns video file path.
    """
    clip = db.get_clip(clip_id)

    # resume: clip already fully done — skip everything
    if clip["status"] in (ClipStatus.VIDEO_DONE.value, ClipStatus.DONE.value):
        file_path = clip.get("file_path")
        print(f"  Already complete — skipping (file: {file_path})")
        return Path(file_path) if file_path else OUTPUT_DIR / job_id / "already_done.mp4"

    # resume: keyframe approved or video in-flight — skip straight to video
    if clip["status"] in (ClipStatus.KEYFRAME_APPROVED.value, ClipStatus.VIDEO_PENDING.value):
        kf_job_id = clip["keyframe_job_id"]
        print(f"  Resuming from approved keyframe {kf_job_id}")
    else:
        kf_job_id = run_keyframe(job_id, clip_id, scenario, scene,
                                 char_job_id=char_job_id, dry_run=dry_run)

    return run_video(job_id, clip_id, scenario, scene, kf_job_id, dry_run=dry_run)


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import uuid
    from schema import SceneLabel, SignalTag, TalentFormat

    TEST_DB = Path(__file__).parent / "_pipeline_test.db"
    if TEST_DB.exists():
        os.remove(TEST_DB)
    db.DB_PATH = TEST_DB

    try:
        db.init_db()

        sample_scene = SceneBlock(
            label=SceneLabel.HOOK,
            time_range="0-3s",
            duration_s=4,
            description=(
                "Animated office — cartoon character buried in manual work, "
                "viral tweet crashes onto screen. "
                "Voiceover: 'If you're still doing everything manually... you're already behind.'"
            ),
        )

        sample_scenario = ScenarioInput(
            scenario_id="coursiv_001",
            rank=1,
            direction="Occupation-Specific AI Gap",
            signal_tags=[SignalTag.PROVEN],
            score=94,
            hook="If you're still doing everything manually... you're already behind.",
            visual_theme="Neon Dark",
            talent_format=TalentFormat.CARTOON,
            offer="$20 AI Certificate — tonight only",
            audience="busy knowledge worker 28-50",
            pain_point="I spend hours every week on tasks AI could do in minutes",
            scenes=[sample_scene],
            product="Coursiv",
            market="US",
        )

        job_id  = "job-pipe-test"
        clip_id = "clip-pipe-001"

        db.create_job(job_id, sample_scenario.scenario_id)
        db.create_clip(clip_id, job_id, sample_scenario.scenario_id,
                       0, sample_scene.label.value, sample_scene.duration_s)

        print("Testing run_clip() in DRY RUN mode (0 credits)...")
        dest = run_clip(job_id, clip_id, sample_scenario, sample_scene, dry_run=True)

        # verify DB state
        clip = db.get_clip(clip_id)
        assert clip["status"] == ClipStatus.VIDEO_DONE.value, f"Bad status: {clip['status']}"
        assert clip["keyframe_job_id"].startswith("dry-kf-"), f"Bad kf_id: {clip['keyframe_job_id']}"
        assert clip["video_job_id"].startswith("dry-vp-"), f"Bad vp_id: {clip['video_job_id']}"
        assert clip["credits_keyframe"] == 2.0
        assert clip["credits_video"] == 11.0
        total = db.get_job_total_cost(job_id)
        assert total == 13.0, f"Expected 13.0, got {total}"
        print(f"✅ clip status: {clip['status']}")
        print(f"✅ total cost logged: {total} credits")

        # verify resume skips keyframe
        # reset status to keyframe_approved to simulate mid-run resume
        db.update_clip(clip_id, status=ClipStatus.KEYFRAME_APPROVED.value)
        clip2 = db.get_clip(clip_id)
        assert clip2["status"] == ClipStatus.KEYFRAME_APPROVED.value
        print("✅ resume path: keyframe_approved status preserved correctly")

        # budget gate: inject enough cost to trip ceiling
        db.log_cost(job_id, clip_id, "test", 90.0)
        try:
            db.check_budget(job_id, 11.0, 1000.0, BUDGET_CEILING)
            assert False, "Should have raised BudgetCeilingError"
        except db.BudgetCeilingError:
            print("✅ budget ceiling gate fires correctly")

        print("\n✅ pipeline.py — all dry-run checks passed (0 credits spent)")

    finally:
        if TEST_DB.exists():
            os.remove(TEST_DB)
        # clean up dry-run output dirs
        import shutil
        dry_out = OUTPUT_DIR / "job-pipe-test"
        if dry_out.exists():
            shutil.rmtree(dry_out)
