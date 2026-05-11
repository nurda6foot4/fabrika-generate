"""
main.py — Entry point. Parses Fabrika HTML → runs full pipeline per scenario.
Usage:
  python3 main.py <html_file> [--dry-run] [--scenario N] [--ceiling CREDITS]

  --dry-run       No real credits spent; DB still written; output paths are placeholders.
  --scenario N    Process only scenario rank N (1-based). Omit to process all.
  --ceiling N     Per-job budget ceiling in credits (default: 100).
"""

from __future__ import annotations
import argparse
import re
import sys
import uuid
from pathlib import Path

from bs4 import BeautifulSoup

import character
import db
import pipeline
import postprocess
import translator as _translator
from schema import (
    ScenarioInput, SceneBlock, SceneLabel, SignalTag, TalentFormat,
    ClipStatus,
)

# ── Speech-aware scene expansion ──────────────────────────────────────────────

_WORDS_PER_SECOND = 2.3
_MAX_WORDS_PER_CLIP = int(8.0 * _WORDS_PER_SECOND)   # 18 words fits in 8s
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')
# Matches "Person A: 'speech'" or "Person B: 'speech'" blocks
_SPEAKER_BLOCK_RE = re.compile(
    r"(Person\s+[A-Z]\w*):\s*'((?:[^']|'(?=[a-z]))*)'",
    re.IGNORECASE | re.DOTALL,
)

def _snap_dur(seconds: float) -> int:
    if seconds <= 4.5:
        return 4
    if seconds <= 6.5:
        return 6
    return 8

def _split_speech_to_clips(
    speech: str, visual: str, label: SceneLabel, time_range: str
) -> list[SceneBlock]:
    """Split speech at sentence boundaries into clips that each fit within 8s."""
    sentences = _SENT_SPLIT.split(speech.strip())
    groups: list[list[str]] = []
    current: list[str] = []

    for sent in sentences:
        words = sent.split()
        if current and len(current) + len(words) > _MAX_WORDS_PER_CLIP:
            groups.append(current)
            current = words
        else:
            current.extend(words)
    if current:
        groups.append(current)

    clips = []
    for group in groups:
        text    = " ".join(group)
        snapped = _snap_dur(len(group) / _WORDS_PER_SECOND)
        desc    = f"{visual}. Voiceover: '{text}'" if visual else f"Voiceover: '{text}'"
        clips.append(SceneBlock(label=label, time_range=time_range,
                                duration_s=snapped, description=desc))
    return clips

# Also matches "Person B turns to camera: 'speech'" style action+speech lines
_SPEAKER_ACTION_RE = re.compile(
    r"(Person\s+[A-Z]\w*)\s+[^:\']+:\s*'((?:[^']|'(?=[a-z]))*)'",
    re.IGNORECASE | re.DOTALL,
)

def _expand_dialogue_scene(scene: SceneBlock) -> list[SceneBlock] | None:
    """
    For scenes with explicit Person A/B speaker blocks, expand per-speaker
    so each expanded clip carries a speaker tag for character ref selection.
    Handles both "Person B: 'speech'" and "Person B turns to camera: 'speech'".
    Returns None if no speaker blocks found.
    """
    blocks = [(m.group(1), m.group(2).strip())
              for m in _SPEAKER_BLOCK_RE.finditer(scene.description)]
    # Fallback: catch "Person X [action]: 'speech'" patterns (e.g. CTA "turns to camera")
    if not blocks:
        blocks = [(m.group(1), m.group(2).strip())
                  for m in _SPEAKER_ACTION_RE.finditer(scene.description)]
    if not blocks:
        return None

    clips: list[SceneBlock] = []
    for speaker, speech in blocks:
        words  = speech.split()
        visual = f"{speaker} speaking"
        if len(words) / _WORDS_PER_SECOND <= 8.5:
            snapped = _snap_dur(len(words) / _WORDS_PER_SECOND)
            clips.append(SceneBlock(
                label=scene.label, time_range=scene.time_range,
                duration_s=snapped,
                description=f"{visual}. Voiceover: '{speech}'",
            ))
        else:
            clips.extend(
                _split_speech_to_clips(speech, visual, scene.label, scene.time_range)
            )
    return clips

def _expand_scenes(scenes: list[SceneBlock]) -> list[SceneBlock]:
    """
    Expand scenes whose speech exceeds one clip (8s) into multiple sub-clips.
    Dialogue scenes are expanded per-speaker, preserving speaker attribution.
    """
    expanded: list[SceneBlock] = []
    for scene in scenes:
        # Try dialogue-aware expansion first (preserves speaker tags)
        dialogue_clips = _expand_dialogue_scene(scene)
        if dialogue_clips:
            expanded.extend(dialogue_clips)
            continue

        speech = _translator._extract_speech(scene.description)
        if not speech or len(speech.split()) / _WORDS_PER_SECOND <= 8.5:
            expanded.append(scene)
            continue

        visual = _translator._clean_scene(scene.description)
        if len(visual) < 5:
            visual = "dialogue exchange, two-person conversation"
        expanded.extend(
            _split_speech_to_clips(speech, visual, scene.label, scene.time_range)
        )
    return expanded


# ── Talent format mapper ───────────────────────────────────────────────────

_TALENT_MAP: list[tuple[str, TalentFormat]] = [
    ("cartoon",    TalentFormat.CARTOON),
    ("ai-generat", TalentFormat.CARTOON),
    ("asmr",       TalentFormat.ASMR),
    ("silent",     TalentFormat.ASMR),
    ("dialogue",   TalentFormat.DIALOGUE),
    ("podcast",    TalentFormat.DIALOGUE),
    ("two-person", TalentFormat.DIALOGUE),
    ("ugc",        TalentFormat.UGC),
    ("talking head", TalentFormat.UGC),
    ("polished",   TalentFormat.OTHER),
    ("presenter",  TalentFormat.UGC),
]

def _parse_talent(raw: str) -> TalentFormat:
    low = raw.lower()
    for keyword, fmt in _TALENT_MAP:
        if keyword in low:
            return fmt
    return TalentFormat.OTHER


# ── Signal tag mapper ──────────────────────────────────────────────────────

_VALID_TAGS = {t.value for t in SignalTag}

def _parse_tags(tag_texts: list[str]) -> list[SignalTag]:
    result = []
    for t in tag_texts:
        if t in _VALID_TAGS:
            result.append(SignalTag(t))
    return result or [SignalTag.UNTESTED]


# ── Scene parser ───────────────────────────────────────────────────────────

_SCENE_BLOCK_RE = re.compile(
    r'\[(\d+)-(\d+)s\s+(HOOK|PROBLEM|SOLUTION|CTA)\]\s*(.*?)(?=\[\d+|$)',
    re.DOTALL | re.IGNORECASE,
)

def _parse_scenes(suggested_copy: str) -> list[SceneBlock]:
    scenes = []
    for m in _SCENE_BLOCK_RE.finditer(suggested_copy):
        start_s  = int(m.group(1))
        end_s    = int(m.group(2))
        label    = SceneLabel(m.group(3).upper())
        desc     = m.group(4).strip()
        duration = end_s - start_s
        scenes.append(SceneBlock(
            label=label,
            time_range=f"{start_s}-{end_s}s",
            duration_s=duration,   # snapped to 4/6/8 by validator
            description=desc,
        ))
    return scenes


# ── HTML parser ────────────────────────────────────────────────────────────

def parse_fabrika_html(path: Path) -> tuple[str, str, list[ScenarioInput]]:
    """
    Parses a Fabrika Creative Matrix HTML file.
    Returns (product, market, scenarios).
    """
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # product + market from subtitle
    subtitle = soup.find("p", class_="subtitle")
    product = "Unknown"
    market  = "Unknown"
    if subtitle:
        sub_text = subtitle.get_text()
        pm = re.search(r"Product:\s*(\S+)", sub_text)
        mm = re.search(r"Market:\s*(\S+)", sub_text)
        if pm:
            product = pm.group(1).strip(" ·")
        if mm:
            market = mm.group(1).strip(" ·")

    video_tab = soup.find("div", {"id": "video"})
    if not video_tab:
        raise ValueError("No #video tab found in HTML — is this a Fabrika Creative Matrix report?")

    cards = video_tab.find_all("div", recursive=False)
    scenarios: list[ScenarioInput] = []

    for card in cards:
        text_lines = [t.strip() for t in card.get_text(separator="\n").splitlines() if t.strip()]

        # rank — first line like "#1"
        rank = 0
        rank_m = re.match(r"#(\d+)", text_lines[0]) if text_lines else None
        if rank_m:
            rank = int(rank_m.group(1))

        # direction — second non-rank non-empty line before any tag
        direction = text_lines[1] if len(text_lines) > 1 else "Unknown"

        # signal tags — span backgrounds matching tag values
        spans = card.find_all("span")
        raw_tags = [s.get_text(strip=True) for s in spans if s.get_text(strip=True).isupper()
                    and len(s.get_text(strip=True)) <= 10]
        signal_tags = _parse_tags(raw_tags)

        # score
        score = 0
        for line in text_lines:
            if line.isdigit() and int(line) <= 100:
                score = int(line)
                break

        # hook — large quoted text block (border-left indigo style)
        hook_div = card.find("div", style=lambda s: s and "border-left" in str(s))
        hook = hook_div.get_text(strip=True).strip('"') if hook_div else ""

        # grid fields: Plot Structure, Talent Format, Audience
        visual_theme  = ""
        talent_raw    = ""
        audience      = ""
        grid_divs = card.find_all("div", style=lambda s: s and "grid" in str(s))
        for grid in grid_divs:
            for cell in grid.find_all("div", recursive=False):
                label_el = cell.find("div", style=lambda s: s and "color:#666" in str(s))
                val_el   = cell.find("div", style=lambda s: s and "color:#ccc" in str(s))
                if not (label_el and val_el):
                    continue
                label = label_el.get_text(strip=True)
                val   = val_el.get_text(strip=True)
                if label == "Plot Structure":
                    visual_theme = val
                elif label == "Talent Format":
                    talent_raw = val
                elif label == "Audience":
                    audience = val

        talent_format = _parse_talent(talent_raw)

        # pain point
        pain_el = card.find("div", style=lambda s: s and "font-style:italic" in str(s))
        pain_point = ""
        if pain_el:
            raw = pain_el.get_text(strip=True)
            # Use re.sub — NOT lstrip() which strips individual chars, not the prefix string
            pain_point = re.sub(r'^Pain:\s*"?', "", raw).rstrip('"')

        # suggested copy → scenes
        copy_el = card.find("div", style=lambda s: s and "background:#0d1a0d" in str(s))
        suggested_copy = ""
        if copy_el:
            suggested_copy = copy_el.get_text(separator=" ", strip=True)
            # strip "Suggested Copy:" prefix
            suggested_copy = re.sub(r"^Suggested Copy:\s*", "", suggested_copy)
        scenes = _expand_scenes(_parse_scenes(suggested_copy))

        # offer: extract from CTA block text (first sentence with a $ or "certificate")
        offer = ""
        cta_scenes = [s for s in scenes if s.label == SceneLabel.CTA]
        if cta_scenes:
            cta_text = cta_scenes[0].description
            offer_m = re.search(r"\$[\d,]+[^.]*", cta_text)
            if offer_m:
                offer = offer_m.group(0)[:80]
        if not offer:
            offer = "See offer in video"

        if not scenes:
            continue   # skip cards with no parseable scenes

        scenario_id = f"{product.lower()}_{rank:03d}"
        scenarios.append(ScenarioInput(
            scenario_id=scenario_id,
            rank=rank,
            direction=direction,
            signal_tags=signal_tags,
            score=score,
            hook=hook,
            visual_theme=visual_theme or talent_raw or "UGC",
            talent_format=talent_format,
            offer=offer,
            audience=audience,
            pain_point=pain_point,
            scenes=scenes,
            product=product,
            market=market,
        ))

    return product, market, scenarios


# ── Full pipeline runner ───────────────────────────────────────────────────

def run_scenario(scenario: ScenarioInput, *, dry_run: bool) -> None:
    """
    Two-phase pipeline:
      Phase 1 — generate ALL keyframes and collect approvals.
      Phase 2 — generate videos for every approved keyframe.
    Budget ceiling is read from pipeline.BUDGET_CEILING (set by CLI --ceiling flag).
    """
    job_id = str(uuid.uuid4())
    print(f"\n{'='*64}")
    print(f"Scenario: {scenario.scenario_id}  rank={scenario.rank}  score={scenario.score}")
    print(f"Direction: {scenario.direction}")
    print(f"Scenes: {len(scenario.scenes)}  |  Job: {job_id}")
    print(f"{'='*64}")

    db.init_db()
    db.create_job(job_id, scenario.scenario_id)

    # ── Character refs (front + back, once per scenario) ─────────────────
    print(f"\n  Generating character reference portraits...")
    char_sheet = character.generate_character_sheet(scenario, dry_run=dry_run)
    print(f"  Character front: {char_sheet['job_id_front']}")
    print(f"  Character back:  {char_sheet.get('job_id_back', 'n/a')}")

    # create all clip rows up front
    clips = []   # list of (clip_id, idx, scene)
    for idx, scene in enumerate(scenario.scenes):
        clip_id = str(uuid.uuid4())
        db.create_clip(clip_id, job_id, scenario.scenario_id,
                       idx, scene.label.value, scene.duration_s)
        clips.append((clip_id, idx, scene))

    # ── PHASE 1: all keyframes ─────────────────────────────────────────────
    print(f"\n{'─'*64}")
    print(f"  PHASE 1 — generating {len(clips)} keyframes")
    print(f"{'─'*64}")

    approved_clips = []   # (clip_id, idx, scene, kf_job_id)

    for clip_id, idx, scene in clips:
        print(f"\n── Keyframe {idx+1}/{len(clips)}: [{scene.label.value}] {scene.time_range}")
        try:
            kf_job_id = pipeline.run_keyframe(
                job_id, clip_id, scenario, scene,
                char_job_id=character.pick_char_ref(scene.description, char_sheet),
                dry_run=dry_run,
            )
            approved_clips.append((clip_id, idx, scene, kf_job_id))
        except (db.InsufficientCreditsError, db.BudgetCeilingError) as e:
            print(f"\n❌ Budget gate: {e}")
            db.update_clip(clip_id, status=ClipStatus.FAILED.value)
            db.log_error(clip_id, "budget", str(e))
            db.update_job_status(job_id, "failed")
            sys.exit(1)
        except RuntimeError as e:
            print(f"\n❌ Keyframe failed: {e}")
            db.update_clip(clip_id, status=ClipStatus.FAILED.value)
            db.log_error(clip_id, "keyframe", str(e))
            # skip this clip — don't add to approved_clips

    if not approved_clips:
        print("\n❌ No keyframes approved — aborting.")
        db.update_job_status(job_id, "failed")
        return

    # ── PHASE 2: all videos ────────────────────────────────────────────────
    print(f"\n{'─'*64}")
    print(f"  PHASE 2 — generating {len(approved_clips)} videos")
    print(f"{'─'*64}")

    for clip_id, idx, scene, kf_job_id in approved_clips:
        print(f"\n── Video {idx+1}/{len(clips)}: [{scene.label.value}] {scene.time_range} ({scene.duration_s}s)")
        try:
            raw_path = pipeline.run_video(
                job_id, clip_id, scenario, scene, kf_job_id, dry_run=dry_run,
            )

            if not dry_run:
                # encode for Premiere
                dest = pipeline.OUTPUT_DIR / job_id / f"{scenario.scenario_id}_{idx:03d}_01_done.mp4"
                print(f"  Encoding for Premiere → {dest.name}")
                encoded = postprocess.encode_for_premiere(raw_path, dest, scenario.scenario_id, idx)

                # validate
                info = postprocess.validate_output(encoded)
                print(f"  Validated: {info['width']}x{info['height']} {info['codec']} {info['duration_s']}s")

                # sidecar JSON
                clip = db.get_clip(clip_id)
                sidecar_data = {
                    "clip_id": clip_id,
                    "job_id": job_id,
                    "scenario_id": scenario.scenario_id,
                    "clip_index": idx,
                    "scene_label": scene.label.value,
                    "duration_s": scene.duration_s,
                    "keyframe_job_id": clip.get("keyframe_job_id"),
                    "video_job_id": clip.get("video_job_id"),
                    "credits_keyframe": clip.get("credits_keyframe"),
                    "credits_video": clip.get("credits_video"),
                    "keyframe_prompt": pipeline.translator.to_keyframe_params(scenario, scene).prompt,
                    "video_prompt": pipeline.translator.to_video_params(scenario, scene, kf_job_id).prompt,
                    "ffprobe": info,
                }
                sidecar = postprocess.write_sidecar(encoded, sidecar_data)
                db.update_clip(clip_id, status=ClipStatus.DONE.value, file_path=str(encoded))
                print(f"  Sidecar: {sidecar.name}")
            else:
                print(f"  [DRY RUN] video done — no FFmpeg encode in dry-run mode")

        except (db.InsufficientCreditsError, db.BudgetCeilingError) as e:
            print(f"\n❌ Budget gate: {e}")
            db.update_clip(clip_id, status=ClipStatus.FAILED.value)
            db.log_error(clip_id, "budget", str(e))
            db.update_job_status(job_id, "failed")
            sys.exit(1)
        except RuntimeError as e:
            print(f"\n❌ Video failed: {e}")
            db.update_clip(clip_id, status=ClipStatus.FAILED.value)
            db.log_error(clip_id, "video", str(e))
            # continue to next clip

    total_cost = db.get_job_total_cost(job_id)
    db.update_job_status(job_id, "done")
    print(f"\n✅ Scenario {scenario.scenario_id} complete — {len(approved_clips)}/{len(clips)} clips")
    print(f"   Total credits used: {total_cost:.1f}")
    print(f"   Output: {pipeline.OUTPUT_DIR / job_id}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Fabrika-Generate pipeline")
    parser.add_argument("html_file", help="Path to Fabrika Creative Matrix HTML report")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate pipeline without spending credits")
    parser.add_argument("--scenario", type=int, default=None,
                        help="Process only this rank (1-based). Default: process all.")
    parser.add_argument("--ceiling", type=float, default=100.0,
                        help="Per-job budget ceiling in credits (default: 100)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Skip human keyframe approval checkpoint (batch mode)")
    args = parser.parse_args()

    html_path = Path(args.html_file)
    if not html_path.exists():
        sys.exit(f"❌ File not found: {html_path}")

    print(f"Parsing {html_path.name}...")
    product, market, scenarios = parse_fabrika_html(html_path)
    print(f"Product: {product}  Market: {market}  Scenarios found: {len(scenarios)}")

    if args.scenario is not None:
        scenarios = [s for s in scenarios if s.rank == args.scenario]
        if not scenarios:
            sys.exit(f"❌ No scenario with rank {args.scenario} found")

    if args.dry_run:
        print("🔶 DRY RUN — no credits will be spent")

    pipeline.BUDGET_CEILING = args.ceiling
    pipeline.AUTO_APPROVE = args.auto_approve

    for scenario in scenarios:
        run_scenario(scenario, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
