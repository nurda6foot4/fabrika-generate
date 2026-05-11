"""
character.py — Character ID generator.
Generates TWO reference portraits (front + back/side) before keyframe generation.
pick_char_ref() selects which one to pass as --image based on the scene description.

Imports: higgs, schema. No db, no pipeline.
Run standalone: python3 character.py  (costs 4 credits)
"""

from __future__ import annotations
import json
import urllib.request
from pathlib import Path

import higgs
from schema import ScenarioInput, TalentFormat

CHARACTERS_DIR = Path(__file__).parent / "characters"

def _is_dialogue(scenario: ScenarioInput) -> bool:
    """True when the scenario is a two-person dialogue/podcast format."""
    if scenario.talent_format == TalentFormat.DIALOGUE:
        return True
    t = scenario.visual_theme.lower()
    return "dialogue" in t or "podcast" in t or "two-person" in t

# ── Style maps for character sheet generation ──────────────────────────────

# Person A description per format
_CHARACTER_BASE: dict[TalentFormat, str] = {
    TalentFormat.CARTOON: (
        "colorful 2D animated cartoon character, expressive face, clean linework, "
        "consistent character design, office worker personality, "
        "wearing a simple collared shirt, bright office setting"
    ),
    TalentFormat.UGC: (
        "photorealistic man, early-to-mid 30s, relatable and trustworthy face, short dark hair, "
        "wearing a plain navy blue crew-neck t-shirt, "
        "natural skin tone, neat appearance, slight smile, "
        "sitting in a clean home office — bookshelf slightly out of focus in background, "
        "warm soft natural lighting from the left, camera at eye level"
    ),
    TalentFormat.DIALOGUE: (
        "photorealistic man, early 30s, friendly and approachable face, short dark hair, "
        "wearing a navy blue t-shirt, casual style, clean neutral background"
    ),
    TalentFormat.ASMR: (
        "minimal aesthetic, soft neutral tones, pastel background, no character face needed"
    ),
    TalentFormat.OTHER: (
        "photorealistic polished professional presenter, early 30s, confident posture, "
        "wearing a fitted dark navy blazer over a clean white shirt, "
        "sharp groomed appearance, studio lighting, "
        "standing in a clean minimal studio with dark gradient backdrop, "
        "UI screen panel softly visible in background"
    ),
}

# Person B description per format — visually distinct from Person A
_CHARACTER_BASE_B: dict[TalentFormat, str] = {
    TalentFormat.CARTOON: (
        "colorful 2D animated cartoon character, expressive face, clean linework, "
        "female character, different hair color and style from Person A, "
        "wearing a different colored shirt, bright office setting"
    ),
    TalentFormat.UGC: (
        "photorealistic woman, late 20s to early 30s, warm and approachable face, "
        "shoulder-length brown hair, wearing a light grey crew-neck sweater, "
        "natural skin tone, friendly expression, "
        "sitting in a clean home office — bookshelf slightly out of focus in background, "
        "warm soft natural lighting from the left, camera at eye level"
    ),
    TalentFormat.DIALOGUE: (
        "photorealistic woman, late 20s, friendly and approachable face, "
        "shoulder-length brown hair, wearing a light grey sweater, clean neutral background"
    ),
    TalentFormat.ASMR: (
        "minimal aesthetic, soft neutral tones, pastel background, no character face needed"
    ),
    TalentFormat.OTHER: (
        "photorealistic woman, early 30s, confident posture, "
        "wearing a fitted dark blazer over a clean white blouse, "
        "sharp groomed appearance, studio lighting, clean minimal studio backdrop"
    ),
}

_FRONT_SUFFIX = (
    "Single clean front-facing portrait, eye-level camera, subject looking straight ahead. "
    "Plain neutral background. Full clear view of face, hair, and outfit. "
    "One person only, single frame, no panels, no split screen, no collage. "
    "Photorealistic. Character reference for video production. Natural studio lighting."
)

_BACK_SUFFIX = (
    "Single clean back-view portrait, subject facing away from camera, "
    "three-quarter rear angle showing the back of the head, hair, and full outfit. "
    "Plain neutral background. One person only, single frame, no panels, no split screen. "
    "Photorealistic. Character back-reference for video production. Natural studio lighting."
)

# Second dialogue character — visually distinct from the first
_PERSON_B_SUFFIX = (
    "Single clean front-facing portrait, eye-level camera, subject looking straight ahead. "
    "Different hair color, slightly different outfit from Person A. "
    "Plain neutral background. One person only, single frame, no panels, no split screen, no collage. "
    "Photorealistic. Character reference for video production. Natural studio lighting."
)

# Keywords that signal a back/away-facing shot is appropriate
_BACK_KEYWORDS = {
    "turns away", "walks away", "walking away", "back to camera",
    "faces away", "exit", "exits", "leaves", "leaving", "walks off",
    "turns back", "away from camera", "over the shoulder",
}


# ── Character sheet prompt builder ─────────────────────────────────────────

def build_character_prompt(scenario: ScenarioInput, view: str = "front") -> str:
    """
    Builds a character reference prompt.
    view="front"    → Person A front-facing portrait.
    view="back"     → Non-dialogue: Person A rear angle. Dialogue: Person B front portrait.
    view="person_b" → Person B front-facing portrait (explicit).
    """
    is_person_b = (view in ("back", "person_b")) and _is_dialogue(scenario)
    base_map = _CHARACTER_BASE_B if is_person_b else _CHARACTER_BASE
    base = base_map.get(scenario.talent_format,
                        base_map.get(TalentFormat.OTHER, _CHARACTER_BASE[TalentFormat.OTHER]))

    personality = (
        f"Character represents: busy {scenario.audience}. "
        f"Story angle: {scenario.direction}."
    )

    # Non-dialogue back view = rear angle; everything else = front-facing portrait
    if view == "back" and not _is_dialogue(scenario):
        suffix = _BACK_SUFFIX
    else:
        suffix = _FRONT_SUFFIX
    prompt = f"{base}. {personality} {suffix}"
    return prompt[:500]   # keep under model noise threshold


def pick_char_ref(description: str, char_sheet: dict) -> str:
    """
    Returns the job_id to use as --image for this scene's keyframe.

    Dialogue scenarios: alternates between Person A ref (front) and Person B ref (back)
    based on who is the primary speaker in the scene.

    Non-dialogue: uses back ref when scene implies away-facing shot, front otherwise.
    """
    d = description.lower()

    if char_sheet.get("is_dialogue"):
        # Expanded clips carry "Person X speaking." prefix — check that first.
        # For non-expanded scenes, look for who has the first "Person X:" speaking colon.
        if d.startswith("person b"):
            return char_sheet.get("job_id_back") or char_sheet["job_id_front"]
        first_a = d.find("person a:")
        first_b = d.find("person b:")
        if first_b >= 0 and (first_a < 0 or first_b < first_a):
            return char_sheet.get("job_id_back") or char_sheet["job_id_front"]
        return char_sheet["job_id_front"]

    if any(kw in d for kw in _BACK_KEYWORDS):
        return char_sheet.get("job_id_back") or char_sheet["job_id_front"]
    return char_sheet["job_id_front"]


# ── Storage helpers ────────────────────────────────────────────────────────

def _sheet_path(scenario_id: str) -> Path:
    return CHARACTERS_DIR / f"{scenario_id}.json"

def _save_sheet(scenario_id: str, data: dict) -> None:
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    _sheet_path(scenario_id).write_text(
        json.dumps(data, indent=2, ensure_ascii=False)
    )

def load_sheet(scenario_id: str) -> dict | None:
    """Returns saved character sheet dict or None if not generated yet."""
    p = _sheet_path(scenario_id)
    if p.exists():
        return json.loads(p.read_text())
    return None


# ── Main generator ─────────────────────────────────────────────────────────

def generate_character_sheet(
    scenario: ScenarioInput,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Generates (or loads cached) front + back character reference portraits.
    Returns dict with: scenario_id, job_id_front, job_id_back,
                       image_path_front, image_path_back, prompt_front, prompt_back.
    job_id_front is also aliased as job_id for backward compatibility.

    force=True  → regenerate even if cached.
    dry_run=True → skip API calls, return fake data (0 credits).
    """
    is_dialogue = _is_dialogue(scenario)

    if not force and not dry_run:
        cached = load_sheet(scenario.scenario_id)
        if (cached
                and "job_id_front" in cached
                and not cached.get("job_id_front", "").startswith("dry-")):
            mode = "a/b" if is_dialogue else "front/back"
            print(f"  ♻️  Character refs cached ({mode}): {cached['scenario_id']}")
            return cached

    # Dialogue: generate individual portrait for each person (A and B).
    # Other formats: generate front-facing + back/rear-angle of same character.
    if is_dialogue:
        views = [
            ("front", build_character_prompt(scenario, view="front")),   # Person A
            ("back",  build_character_prompt(scenario, view="person_b")),  # Person B
        ]
        label_a, label_b = "Person A", "Person B"
    else:
        views = [
            ("front", build_character_prompt(scenario, view="front")),
            ("back",  build_character_prompt(scenario, view="back")),
        ]
        label_a, label_b = "front", "back"

    if dry_run:
        fake = {
            "scenario_id":      scenario.scenario_id,
            "job_id_front":     f"dry-char-front-{scenario.scenario_id}",
            "job_id_back":      f"dry-char-back-{scenario.scenario_id}",
            "job_id":           f"dry-char-front-{scenario.scenario_id}",
            "image_path_front": "",
            "image_path_back":  "",
            "prompt_front":     views[0][1],
            "prompt_back":      views[1][1],
            "is_dialogue":      is_dialogue,
        }
        _save_sheet(scenario.scenario_id, fake)
        print(f"  [DRY RUN] Character refs skipped ({label_a}/{label_b})")
        return fake

    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    sheet: dict = {"scenario_id": scenario.scenario_id, "is_dialogue": is_dialogue}

    for view, prompt in views:
        label = label_a if view == "front" else label_b
        print(f"  Generating character ref [{label}] for {scenario.scenario_id}...")
        job_id = higgs.generate_image(
            model="nano_banana_2",
            prompt=prompt,
            aspect_ratio="9:16",
        )
        print(f"  Waiting for [{label}] job {job_id}...")
        job_result = higgs.wait_job(job_id)
        urls = higgs.get_result_urls(job_result)
        if not urls:
            raise RuntimeError(f"No URL returned for character [{label}] job {job_id}")
        dest = CHARACTERS_DIR / f"{scenario.scenario_id}_{view}.png"
        urllib.request.urlretrieve(urls[0], dest)
        sheet[f"job_id_{view}"]     = job_id
        sheet[f"image_url_{view}"]  = urls[0]
        sheet[f"image_path_{view}"] = str(dest)
        sheet[f"prompt_{view}"]     = prompt
        print(f"  ✅ [{label}] ref saved: {dest.name}")

    sheet["job_id"] = sheet["job_id_front"]
    _save_sheet(scenario.scenario_id, sheet)
    return sheet


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from schema import SceneLabel, SignalTag, SceneBlock

    sample_scene = SceneBlock(
        label=SceneLabel.HOOK,
        time_range="0-3s",
        duration_s=4,
        description="Animated office — cartoon character buried in manual work.",
    )
    sample_scenario = ScenarioInput(
        scenario_id="char_test_001",
        rank=1,
        direction="Occupation-Specific AI Gap",
        signal_tags=[SignalTag.PROVEN],
        score=94,
        hook="If you're still doing everything manually... you're already behind.",
        visual_theme="Neon Dark",
        talent_format=TalentFormat.CARTOON,
        offer="$20 AI Certificate",
        audience="busy knowledge worker 28-50",
        pain_point="I spend hours on tasks AI could do",
        scenes=[sample_scene],
        product="Coursiv",
        market="US",
    )

    # Test prompt builder — front view
    prompt_f = build_character_prompt(sample_scenario, view="front")
    assert len(prompt_f) > 50, "Front prompt too short"
    assert "single frame" in prompt_f or "single clean" in prompt_f, "No single-frame in front"
    assert "no panels" in prompt_f, "No panel exclusion in front"
    assert "front-facing" in prompt_f, "No front-facing in front prompt"
    print(f"✅ front prompt ({len(prompt_f)} chars): {prompt_f[:80]}...")

    # Test prompt builder — back view
    prompt_b = build_character_prompt(sample_scenario, view="back")
    assert "back" in prompt_b.lower(), "No back reference in back prompt"
    assert "no panels" in prompt_b, "No panel exclusion in back"
    print(f"✅ back prompt ({len(prompt_b)} chars): {prompt_b[:80]}...")

    # Test dry-run
    import shutil
    test_chars_dir = Path(__file__).parent / "_test_characters"
    orig_dir = CHARACTERS_DIR

    import character as _self
    _self.CHARACTERS_DIR = test_chars_dir

    sheet = generate_character_sheet(sample_scenario, dry_run=True)
    assert sheet["job_id_front"].startswith("dry-char-front-"), f"Bad front job_id: {sheet['job_id_front']}"
    assert sheet["job_id_back"].startswith("dry-char-back-"),   f"Bad back job_id: {sheet['job_id_back']}"
    assert sheet["job_id"] == sheet["job_id_front"], "compat alias mismatch"
    assert _sheet_path(sample_scenario.scenario_id).exists(), "Sheet JSON not saved"

    # Test cache load
    cached = load_sheet(sample_scenario.scenario_id)
    assert cached is not None, "Cache not found"
    assert cached["scenario_id"] == sample_scenario.scenario_id
    assert "job_id_front" in cached, "job_id_front missing from cache"
    print(f"✅ dry-run + cache: front={sheet['job_id_front']} back={sheet['job_id_back']}")

    # Test pick_char_ref
    assert pick_char_ref("walks away from camera", sheet) == sheet["job_id_back"]
    assert pick_char_ref("turns away", sheet) == sheet["job_id_back"]
    assert pick_char_ref("speaking directly to camera", sheet) == sheet["job_id_front"]
    assert pick_char_ref("hook moment", sheet) == sheet["job_id_front"]
    print(f"✅ pick_char_ref: back on 'walks away', front on 'hook moment'")

    # cleanup
    shutil.rmtree(test_chars_dir, ignore_errors=True)
    _self.CHARACTERS_DIR = orig_dir

    print("\n✅ character.py — all checks passed (0 credits)")
