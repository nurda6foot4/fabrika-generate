"""
postprocess.py — FFmpeg encode + validate + sidecar JSON.
Stdlib only: subprocess, json, pathlib. No schema/db/higgs imports.
Run standalone: python3 postprocess.py  (tests on a synthetic file)
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path


FFMPEG  = "ffmpeg"
FFPROBE = "ffprobe"


# ── FFmpeg encode ──────────────────────────────────────────────────────────

def encode_for_premiere(
    src: Path,
    dest: Path,
    scenario_id: str,
    clip_index: int,
    take: int = 1,
) -> Path:
    """
    Transcode src → dest as H.264 + silent AAC, 9:16, 2K vertical.
    dest filename follows: {scenario_id}_{clip_index:03d}_{take:02d}_done.mp4
    Returns dest path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    # preserve veo3_1 native audio — do not replace with silence
    cmd = [
        FFMPEG, "-y",
        "-i", str(src),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (exit {result.returncode}):\n{result.stderr[-1000:]}"
        )
    return dest


# ── Validate output ────────────────────────────────────────────────────────

class ValidationError(Exception):
    pass

def validate_output(path: Path) -> dict:
    """
    Probes the file with ffprobe. Returns stream info dict.
    Raises ValidationError if resolution, codec, or duration is wrong.
    """
    cmd = [
        FFPROBE, "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValidationError(f"ffprobe failed: {result.stderr[:500]}")

    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s["codec_type"] == "video"), None)
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)

    if video is None:
        raise ValidationError("No video stream found")
    if audio is None:
        print("  [WARN] No audio stream — veo3_1 may not have generated speech for this clip")

    w, h = int(video["width"]), int(video["height"])
    if w != 1080 or h != 1920:
        raise ValidationError(f"Resolution {w}x{h} — expected 1080x1920")

    codec = video.get("codec_name", "")
    if codec != "h264":
        raise ValidationError(f"Codec '{codec}' — expected h264")

    duration = float(info["format"].get("duration", 0))
    if duration < 3.0:
        raise ValidationError(f"Duration {duration:.1f}s is suspiciously short")

    return {
        "width": w,
        "height": h,
        "codec": codec,
        "duration_s": round(duration, 2),
        "audio_codec": audio.get("codec_name"),
        "file_size_mb": round(path.stat().st_size / 1_048_576, 2),
    }


# ── Sidecar JSON ───────────────────────────────────────────────────────────

def write_sidecar(dest: Path, data: dict) -> Path:
    """
    Writes {dest.stem}.json next to the video file.
    data = any provenance dict (clip_id, job_ids, credits, prompts, etc.)
    Returns sidecar path.
    """
    sidecar = dest.with_suffix(".json")
    sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return sidecar


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())

    try:
        # Create a minimal synthetic video with ffmpeg (1s, black frame, 9:16 720p)
        raw_src = tmp / "synthetic_raw.mp4"
        synth_cmd = [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=540x960:r=24",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "5",
            str(raw_src),
        ]
        r = subprocess.run(synth_cmd, capture_output=True)
        if r.returncode != 0:
            raise SystemExit(f"❌ Could not create synthetic test video: {r.stderr.decode()[-500:]}")
        print(f"✅ synthetic source created: {raw_src.stat().st_size // 1024} KB")

        # Encode
        dest = tmp / "coursiv_001_000_01_done.mp4"
        out = encode_for_premiere(raw_src, dest, "coursiv_001", 0, 1)
        assert out.exists(), "dest file not created"
        print(f"✅ encode_for_premiere → {out.stat().st_size // 1024} KB")

        # Validate
        info = validate_output(dest)
        assert info["width"] == 1080,  f"width {info['width']}"
        assert info["height"] == 1920, f"height {info['height']}"
        assert info["codec"] == "h264", f"codec {info['codec']}"
        assert info["audio_codec"] == "aac", f"audio {info['audio_codec']}"
        assert info["duration_s"] >= 3.0, f"duration {info['duration_s']}"
        print(f"✅ validate_output: {info}")

        # Sidecar
        sidecar = write_sidecar(dest, {
            "clip_id": "clip-test-001",
            "scenario_id": "coursiv_001",
            "clip_index": 0,
            "credits_keyframe": 2.0,
            "credits_video": 11.0,
            "keyframe_job_id": "fake-kf",
            "video_job_id": "fake-vp",
        })
        assert sidecar.exists()
        loaded = json.loads(sidecar.read_text())
        assert loaded["clip_id"] == "clip-test-001"
        print(f"✅ write_sidecar: {sidecar.name}")

        # ValidationError path
        try:
            validate_output(raw_src)   # raw is 405x720, not 1080x1920 → should fail
            assert False, "Expected ValidationError"
        except ValidationError as e:
            print(f"✅ ValidationError raised correctly: {e}")

        print("\n✅ postprocess.py — all checks passed")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
