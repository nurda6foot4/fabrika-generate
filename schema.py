"""
schema.py — Pydantic models only. No DB, no CLI, no side effects.
Run standalone to verify: python3 schema.py
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class SignalTag(str, Enum):
    PROVEN    = "PROVEN"
    CONTESTED = "CONTESTED"
    EMERGING  = "EMERGING"
    UNTESTED  = "UNTESTED"

class TalentFormat(str, Enum):
    CARTOON   = "AI-generated cartoon characters"
    DIALOGUE  = "Two-Person Dialogue / Podcast"
    UGC       = "UGC Presenter"
    ASMR      = "Silent ASMR / Text-Only"
    OTHER     = "Other"

class SceneLabel(str, Enum):
    HOOK     = "HOOK"
    PROBLEM  = "PROBLEM"
    SOLUTION = "SOLUTION"
    CTA      = "CTA"

class ClipStatus(str, Enum):
    PENDING            = "pending"
    KEYFRAME_PENDING   = "keyframe_pending"
    KEYFRAME_APPROVED  = "keyframe_approved"
    KEYFRAME_REJECTED  = "keyframe_rejected"
    VIDEO_PENDING      = "video_pending"
    VIDEO_DONE         = "video_done"
    FAILED             = "failed"
    DONE               = "done"

class PolicyResult(str, Enum):
    PASS    = "PASS"
    FIXABLE = "FIXABLE"   # bad wording — can be rewritten
    RANDOM  = "RANDOM"    # model-side non-deterministic rejection


# ── Source data (from Fabrika HTML) ────────────────────────────────────────

class SceneBlock(BaseModel):
    """One time-coded section from the suggested_copy script."""
    label:       SceneLabel
    time_range:  str          # e.g. "0-3s", "3-15s"
    duration_s:  int          # 4, 6, or 8 — rounded to nearest Veo option
    description: str          # raw scene description from Fabrika

    @field_validator("duration_s")
    @classmethod
    def snap_to_veo_durations(cls, v: int) -> int:
        # veo3_1 supports 4, 6, 8s only
        if v <= 4:
            return 4
        elif v <= 6:
            return 6
        else:
            return 8


class ScenarioInput(BaseModel):
    """
    One video creative from Fabrika's Creative Matrix.
    Populated by parsing the HTML output.
    """
    scenario_id:        str               # e.g. "coursiv_001"
    rank:               int
    direction:          str               # e.g. "Occupation-Specific AI Gap"
    signal_tags:        list[SignalTag]
    score:              int               # 0-100
    hook:               str               # the opening line shown to audience
    visual_theme:       str               # e.g. "Neon Dark", "Dragon/Asian Street"
    talent_format:      TalentFormat
    offer:              str               # e.g. "$20 AI Certificate — tonight only"
    audience:           str               # e.g. "busy knowledge worker 28-50"
    pain_point:         str
    scenes:             list[SceneBlock]  # ordered clips from suggested_copy
    product:            str               # e.g. "Coursiv"
    market:             str               # e.g. "US"


# ── Internal job state ─────────────────────────────────────────────────────

class ClipJob(BaseModel):
    """Tracks one clip through the full pipeline."""
    clip_id:          str            # uuid
    job_id:           str            # parent job uuid
    scenario_id:      str
    clip_index:       int            # 0-based position in scenario.scenes
    scene_label:      SceneLabel
    duration_s:       int
    status:           ClipStatus = ClipStatus.PENDING
    retry_count:      int = 0        # max 1 — enforced in pipeline.py
    keyframe_job_id:  Optional[str] = None
    video_job_id:     Optional[str] = None
    file_path:        Optional[str] = None
    credits_keyframe: Optional[float] = None
    credits_video:    Optional[float] = None

    @property
    def credits_total(self) -> float:
        return (self.credits_keyframe or 0) + (self.credits_video or 0)


# ── Translator output (goes to higgs.py) ──────────────────────────────────

class KeyframeParams(BaseModel):
    """Params for: higgsfield generate create nano_banana_2"""
    model:        str = "nano_banana_2"
    prompt:       str
    aspect_ratio: str = "9:16"

class VideoParams(BaseModel):
    """Params for: higgsfield generate create veo3_1"""
    model:        str = "veo3_1"
    prompt:       str
    duration:     int          # 4, 6, or 8
    aspect_ratio: str = "9:16"
    image_ref:    str          # job_id from approved keyframe


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_scene = SceneBlock(
        label=SceneLabel.HOOK,
        time_range="0-3s",
        duration_s=4,
        description="Animated office — cartoon character buried in manual work.",
    )

    sample_scenario = ScenarioInput(
        scenario_id="coursiv_001",
        rank=1,
        direction="Occupation-Specific AI Gap",
        signal_tags=[SignalTag.PROVEN, SignalTag.CONTESTED],
        score=94,
        hook="If you're still doing everything manually... you're already behind.",
        visual_theme="AI-generated cartoon characters",
        talent_format=TalentFormat.CARTOON,
        offer="$20 AI Certificate — tonight only",
        audience="busy knowledge worker 28-50",
        pain_point="I spend hours every week on tasks that AI could probably do in minutes",
        scenes=[sample_scene],
        product="Coursiv",
        market="US",
    )

    sample_clip = ClipJob(
        clip_id="abc-123",
        job_id="job-456",
        scenario_id="coursiv_001",
        clip_index=0,
        scene_label=SceneLabel.HOOK,
        duration_s=4,
    )

    sample_keyframe = KeyframeParams(
        prompt="Animated office scene, cartoon character buried in paperwork, neon dark aesthetic, 9:16 vertical",
    )

    sample_video = VideoParams(
        prompt="Cartoon character buried in manual work, animated office, urgent mood",
        duration=4,
        image_ref="some-keyframe-job-id",
    )

    # duration snap test
    assert SceneBlock(label=SceneLabel.HOOK, time_range="0-3s", duration_s=3, description="test").duration_s == 4
    assert SceneBlock(label=SceneLabel.PROBLEM, time_range="3-15s", duration_s=12, description="test").duration_s == 8

    print("✅ schema.py — all models valid")
    print(f"   ScenarioInput: {sample_scenario.scenario_id}, score={sample_scenario.score}")
    print(f"   ClipJob: {sample_clip.clip_id}, status={sample_clip.status}")
    print(f"   KeyframeParams model: {sample_keyframe.model}")
    print(f"   VideoParams duration snap: 3s→{SceneBlock(label=SceneLabel.HOOK, time_range='0-3s', duration_s=3, description='x').duration_s}s")
