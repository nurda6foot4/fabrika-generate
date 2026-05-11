"""
translator.py — Rules-based. schema.py import only. No CLI, no DB, no side effects.
Converts ScenarioInput + SceneBlock → KeyframeParams + VideoParams + PolicyResult.
Run standalone: python3 translator.py
"""

from __future__ import annotations
import re
from schema import (
    ScenarioInput, SceneBlock, SceneLabel, TalentFormat,
    KeyframeParams, VideoParams, PolicyResult,
)


# ── Prompt sanitizer ──────────────────────────────────────────────────────

_UNSAFE_CHARS = str.maketrans({
    '—': ', ',   # em dash
    '–': ', ',   # en dash
    '…': '. ',   # ellipsis
    '‘': "'",    # left single quote
    '’': "'",    # right single quote
    '“': '"',    # left double quote
    '”': '"',    # right double quote
    '·': ', ',   # middle dot
    '•': ', ',   # bullet
})

def _sanitize(text: str) -> str:
    """Replace chars veo3_1 rejects. Strip remaining non-ASCII."""
    text = text.translate(_UNSAFE_CHARS)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    return re.sub(r'\s{2,}', ' ', text).strip()


# ── Visual style maps ──────────────────────────────────────────────────────

_TALENT_STYLE: dict[TalentFormat, str] = {
    TalentFormat.CARTOON:  "colorful 2D animated cartoon character, expressive face, clean linework",
    TalentFormat.DIALOGUE: "one person speaking naturally, other person's back partially visible in foreground, over-the-shoulder UGC style",
    TalentFormat.UGC:      "single presenter speaking directly to camera, natural home office lighting",
    TalentFormat.ASMR:     "close-up text overlay on minimalist background, no visible character",
    TalentFormat.OTHER:    "polished professional presenter, studio lighting, clean backdrop, business casual attire",
}

_THEME_ENV: dict[str, str] = {
    "neon dark":           "dark background, neon purple and blue lighting, moody cinematic atmosphere",
    "dragon/asian street": "neon-lit asian street at night, lanterns, vibrant colors, cinematic depth",
    "ai-generated cartoon characters": "bright animated office environment, clean cartoon aesthetic",
    "minimal":             "clean white background, minimal props, professional studio feel",
    "ugc":                 "natural home or office background, handheld feel, authentic",
    "twitter/x reaction":  "clean modern studio, dark gradient backdrop, soft blue accent lighting, UI overlay panel visible beside presenter",
    "twitter":             "clean modern studio, dark gradient backdrop, soft blue accent lighting, UI overlay panel visible beside presenter",
    "polished":            "professional studio setup, clean dark backdrop, product UI screen visible in background, sharp studio lighting",
    "dialogue":            "two people in conversation, podcast-style warm setup, sitting facing each other, natural fill lighting, authentic UGC feel",
    "podcast":             "two people in conversation, podcast-style warm setup, sitting facing each other, natural fill lighting, authentic UGC feel",
}

_SCENE_MOOD: dict[SceneLabel, str] = {
    SceneLabel.HOOK:     "urgent attention-grabbing, high energy, strong visual impact",
    SceneLabel.PROBLEM:  "tension and relatable frustration, empathetic, building discomfort",
    SceneLabel.SOLUTION: "relief and transformation, hopeful, energetic progression",
    SceneLabel.CTA:      "confident and direct, clear and punchy, final motivating beat",
}

_SCENE_PACING: dict[SceneLabel, str] = {
    SceneLabel.HOOK:     "fast cuts, dynamic motion, immediate visual hook",
    SceneLabel.PROBLEM:  "medium pace, emotional buildup, relatable moments",
    SceneLabel.SOLUTION: "upbeat pacing, positive transformation, smooth progression",
    SceneLabel.CTA:      "direct and punchy, confident delivery, clear call to action",
}

_CAMERA: dict[TalentFormat, str] = {
    TalentFormat.CARTOON:  "medium shot, character facing viewer, straight-on eye-level camera",
    TalentFormat.DIALOGUE: "two-shot then alternating close-ups, podcast framing",
    TalentFormat.UGC:      "medium close-up, slight handheld feel, direct eye contact",
    TalentFormat.ASMR:     "static close-up, text appears in frame, no camera movement",
    TalentFormat.OTHER:    "medium shot, stable tripod framing, eye level",
}

# ── Content-aware shot selector ────────────────────────────────────────────

def _select_shot(description: str, label: SceneLabel) -> str:
    """
    Content-aware shot selection — UGC natural style.
    Varies framing: waist-up mid shot, half body, chest-up, close-up.
    Never forces full body standing.
    """
    d = description.lower()

    # Dialogue scenes — single speaker in focus, listener's back/shoulder in foreground
    if ("person a" in d or "person b" in d or "speaker a" in d or "speaker b" in d
            or "dialogue exchange" in d or "two-person conversation" in d):
        if label == SceneLabel.HOOK:
            return "over-the-shoulder shot, speaker in sharp focus, listener's back blurred in foreground, handheld UGC"
        if label == SceneLabel.CTA:
            return "tight chest-up close-up on speaker, direct eye contact, listener's shoulder barely visible, final beat"
        if label == SceneLabel.PROBLEM:
            return "over-the-shoulder medium shot, speaker's face and torso in focus, warm natural light, authentic UGC"
        if label == SceneLabel.SOLUTION:
            return "medium close-up on speaker, slight smile, listener's back softly framed in foreground, warm light"
        return "over-the-shoulder shot, speaker in focus, listener's back visible in foreground, handheld UGC"

    if label == SceneLabel.HOOK:
        if "holds" in d or "phone" in d or "shows" in d:
            return "waist-up mid shot, slight handheld feel, subject holds prop toward camera"
        return "chest-up shot, natural handheld framing, subject slightly off-center, UGC feel"
    if label == SceneLabel.CTA:
        if "direct" in d or "eye" in d or "confidence" in d:
            return "tight chest-to-head close-up, direct eye contact, slight forward lean"
        return "close-up, face fills upper two-thirds of frame, final beat"
    if label == SceneLabel.PROBLEM:
        if "react" in d or "reads" in d or "tweet" in d:
            return "half-body shot, subject reacts naturally, slight handheld shake, authentic"
        if "gap" in d or "honest" in d or "falling" in d or "widening" in d:
            return "chest-up close-up, tension on face, intimate handheld framing"
        if "hours" in d or "spending" in d or "work" in d:
            return "medium shot waist-up, subject gestures naturally while speaking"
        return "half-body shot, natural delivery, slight handheld movement"
    if label == SceneLabel.SOLUTION:
        if "overlay" in d or "screen" in d or "template" in d or "tracker" in d:
            return "half-body shot, subject gestures toward off-screen display, natural stance"
        if "certificate" in d or "walk away" in d:
            return "chest-up shot, warm confident delivery, soft background"
        if "minutes" in d or "day" in d or "busy" in d:
            return "medium close-up, relaxed and reassuring tone, handheld feel"
        return "waist-up mid shot, positive open body language, natural lighting"
    return "half-body shot, natural handheld feel, authentic UGC framing"


# ── Scene description cleaner ──────────────────────────────────────────────

# Single-quoted speech block — contraction-aware (you're, I've, don't are NOT closing quotes).
# Apostrophe followed by lowercase = contraction → keep matching.
# Apostrophe NOT followed by lowercase = closing quote → stop.
_SQ = r"'(?:[^']|'(?=[a-z]))*'"   # reusable single-quote block
_DQ = r'"[^"]*"'                   # double-quote block (no contractions in double quotes)

_VOICEOVER_PATTERNS = [
    re.compile(r"Voiceover:\s*(?:" + _SQ + r"|" + _DQ + r")", re.IGNORECASE | re.DOTALL),
    re.compile(r"VO:\s*(?:" + _SQ + r"|" + _DQ + r")",        re.IGNORECASE | re.DOTALL),
    re.compile(r"Caption:\s*(?:" + _SQ + r"|" + _DQ + r")",   re.IGNORECASE | re.DOTALL),
    re.compile(r"Stat (?:appears|overlay):\s*" + _SQ,          re.IGNORECASE | re.DOTALL),
    re.compile(r"\[.*?\]"),                                     # bracketed meta notes like [CTA]
    re.compile(r"Speaker [AB]:\s*(?:" + _SQ + r"|" + _DQ + r")", re.IGNORECASE | re.DOTALL),
    re.compile(r"Person\s+[A-Z]\w*:\s*(?:" + _SQ + r"|" + _DQ + r")", re.IGNORECASE | re.DOTALL),
    re.compile(r"[—–]\s*(?:" + _SQ + r"|" + _DQ + r")", re.DOTALL),   # em/en dash + quoted speech
]

# Matches speech in single quotes that may contain contractions (you're, I've, don't).
# Key: apostrophe followed by a lowercase letter = contraction (keep going).
#      apostrophe NOT followed by lowercase = closing quote (stop).
# Applied separately so it can handle the Fabrika delivery format precisely.
_SPEECH_IN_QUOTES = re.compile(
    # label: 'speech...' — colon then single-quoted block.
    # Allows contractions (you're, I've) by treating apostrophe+lowercase as non-closing.
    # Also strips any dangling word fragments left after the closing quote (e.g. "re still").
    r":\s*'(?:[^']|'(?=[a-z]))*'\.?\s*(?:[a-z]\S*\s*)*",
    re.DOTALL,
)
_STANDALONE_SPEECH = re.compile(
    r"^\s*'(?:[^']|'(?=[a-z]))*'\.?",   # 'Entire scene is dialogue.'
    re.DOTALL,
)

def _clean_scene(description: str) -> str:
    """Strip voiceover/dialogue — keep only visual stage directions."""
    text = description
    for pattern in _VOICEOVER_PATTERNS:
        text = pattern.sub("", text)
    text = _SPEECH_IN_QUOTES.sub(" ", text)
    text = _STANDALONE_SPEECH.sub("", text)
    text = re.sub(r"\bPerson\s+[A-Z]\w*\b", "", text)   # strip orphan speaker labels
    text = re.sub(r"\s{2,}", " ", text).strip(" .,|—:")
    return text[:300]


# Extracts quoted speech lines from scene — opposite of _clean_scene.
# Matches: `Delivery: 'text'`, `Person A: 'text'`, `says directly: 'text'`, standalone `'text'`
_SPEECH_LABEL_RE = re.compile(
    r"(?:delivery|aloud|reacts?|confidence|address(?:es)?|voiceover|vo"
    r"|Person\s+[A-Z]\w*"        # Person A, Person B, Person Host, etc.
    r"|says\s+\w+"               # says directly, says quietly, says aloud, etc.
    r"|turns?\s+to\s+camera"    # turns to camera, turn to camera
    r"):\s*'((?:[^']|'(?=[a-z]))*)'",
    re.IGNORECASE | re.DOTALL,
)
_SPEECH_STANDALONE_RE = re.compile(
    r"^\s*'((?:[^']|'(?=[a-z]))*)'",
    re.DOTALL,
)
# Em/en dash immediately before single-quoted speech: — 'text'
_SPEECH_DASH_RE = re.compile(
    r"[—–]\s*'((?:[^']|'(?=[a-z]))*)'",
    re.DOTALL,
)

def _extract_speech(description: str) -> str:
    """Extract all spoken dialogue lines from scene description."""
    lines = [m.group(1).strip() for m in _SPEECH_LABEL_RE.finditer(description)]
    lines += [m.group(1).strip() for m in _SPEECH_DASH_RE.finditer(description)]
    if not lines:
        m = _SPEECH_STANDALONE_RE.match(description)
        if m:
            lines.append(m.group(1).strip())
    return " ".join(lines)[:350]


def _resolve_theme(visual_theme: str) -> str:
    key = visual_theme.lower().strip()
    for k, v in _THEME_ENV.items():
        if k in key:
            return v
    return f"{visual_theme} visual style, cinematic composition"


# ── Keyframe translator ────────────────────────────────────────────────────

def _is_dialogue_theme(visual_theme: str) -> bool:
    t = visual_theme.lower()
    return "dialogue" in t or "podcast" in t or "two-person" in t


def to_keyframe_params(scenario: ScenarioInput, scene: SceneBlock) -> KeyframeParams:
    """
    Builds the nano_banana_2 image prompt for one scene's opening frame.
    Formula: talent style + environment + scene visual + mood + tech spec.
    """
    talent_desc = _TALENT_STYLE.get(scenario.talent_format,
                                    _TALENT_STYLE[TalentFormat.OTHER])
    if _is_dialogue_theme(scenario.visual_theme):
        talent_desc = _TALENT_STYLE[TalentFormat.DIALOGUE]
    env_desc    = _resolve_theme(scenario.visual_theme)
    visual_beat = _sanitize(_clean_scene(scene.description))
    mood        = _SCENE_MOOD[scene.label]
    shot        = _select_shot(scene.description, scene.label)

    if len(visual_beat) < 15:
        if _is_dialogue_theme(scenario.visual_theme) or "dialogue exchange" in scene.description.lower():
            visual_beat = f"{scene.label.value.lower()} moment, two people in natural conversation"
        else:
            visual_beat = f"{scene.label.value.lower()} moment, presenter speaking directly to camera"

    prompt = (
        f"{talent_desc}. "
        f"Scene: {visual_beat}. "
        f"Environment: {env_desc}. "
        f"Shot: {shot}. Mood: {mood}. "
        f"Single continuous scene, one moment in time, one location. "
        f"Eye-level framing, vertical composition, authentic natural feel. "
        f"negative: split screen, panels, collage, storyboard, multiple frames, "
        f"side by side, reference sheet, bird's eye view, overhead, top-down, tilted angle. "
        f"9:16 vertical format, 2K resolution, high quality, no text overlays."
    )

    return KeyframeParams(
        model="nano_banana_2",
        prompt=prompt,
        aspect_ratio="9:16",
    )


# ── Video translator ───────────────────────────────────────────────────────

def to_video_params(scenario: ScenarioInput, scene: SceneBlock,
                    keyframe_job_id: str) -> VideoParams:
    """
    Builds the veo3_1 image-to-video prompt.
    Formula: scene action + character consistency note + pacing + mood.
    """
    visual_beat = _sanitize(_clean_scene(scene.description))
    if len(visual_beat) < 15:
        if _is_dialogue_theme(scenario.visual_theme) or "dialogue exchange" in scene.description.lower():
            visual_beat = f"{scene.label.value.lower()} moment, two people in natural conversation"
        else:
            visual_beat = f"{scene.label.value.lower()} moment, presenter speaking directly to camera"
    speech      = _sanitize(_extract_speech(scene.description))
    pacing      = _SCENE_PACING[scene.label]
    mood        = _SCENE_MOOD[scene.label]
    shot        = _select_shot(scene.description, scene.label)
    env_desc    = _resolve_theme(scenario.visual_theme)
    talent_desc = _TALENT_STYLE.get(scenario.talent_format,
                                    _TALENT_STYLE[TalentFormat.OTHER])
    if _is_dialogue_theme(scenario.visual_theme):
        talent_desc = _TALENT_STYLE[TalentFormat.DIALOGUE]

    speech_line = (
        f' Character speaks out loud exactly: "{speech}" '
        f'— generate realistic spoken voice audio matching the delivery.'
    ) if speech else ""

    prompt = (
        f"Animate the scene: {visual_beat}.{speech_line} "
        f"Style: {talent_desc}, {env_desc}. "
        f"Shot: {shot}. Pacing: {pacing}. Mood: {mood}. "
        f"Maintain character and environment consistency with the reference keyframe. "
        f"Vertical 9:16 aspect ratio. Smooth motion, no abrupt cuts. "
        f"Generate natural spoken voice audio for all dialogue."
    )

    return VideoParams(
        model="veo3_1",
        prompt=prompt,
        duration=scene.duration_s,
        aspect_ratio="9:16",
        image_ref=keyframe_job_id,
    )


# ── Policy pre-screen ──────────────────────────────────────────────────────

# Hard blocklist: generates RANDOM result — do not auto-retry with same prompt
_POLICY_HARD = {
    "nude", "naked", "explicit", "sexual", "porn",
    "violence", "violent", "blood", "gore",
    "weapon", "gun", "knife", "bomb",
    "murder", "kill", "torture", "assault",
    "cocaine", "heroin", "drug overdose",
    "suicide", "self-harm",
    "child", "minor", "underage",
}

# Soft blocklist: FIXABLE — reword the prompt at brief stage
# Include common conjugations so word-boundary matching catches them all
_POLICY_SOFT = {
    "die", "dies", "died", "dying", "death",  # "offer dies tonight" → rephrase to "offer ends"
    "dangerous", "illegal",
    "hate", "hates", "hated", "hating",
    "racist", "discrimination",
    # NOTE: "weapon" intentionally omitted — already in _POLICY_HARD above
}

def policy_prescreen(params: KeyframeParams | VideoParams) -> tuple[PolicyResult, str]:
    """
    Checks generated prompt against Veo content policy blocklists.
    Returns (PolicyResult, reason_str).
    reason_str is empty string on PASS.
    Uses word-boundary matching to avoid false positives like
    'kill' in 'skilled', 'die' in 'studies', 'hate' in 'whatever'.
    """
    text = params.prompt.lower()

    for word in _POLICY_HARD:
        if re.search(r'\b' + re.escape(word) + r'\b', text):
            return (PolicyResult.RANDOM,
                    f"Hard policy hit: '{word}' — regenerate with different framing")

    for word in _POLICY_SOFT:
        if re.search(r'\b' + re.escape(word) + r'\b', text):
            return (PolicyResult.FIXABLE,
                    f"Soft policy risk: '{word}' — rephrase at brief stage before retrying")

    return (PolicyResult.PASS, "")


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from schema import SignalTag, ClipStatus

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
        signal_tags=[SignalTag.PROVEN, SignalTag.CONTESTED],
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

    # Keyframe
    kf = to_keyframe_params(sample_scenario, sample_scene)
    assert kf.model == "nano_banana_2"
    assert kf.aspect_ratio == "9:16"
    assert "cartoon" in kf.prompt.lower()
    assert "neon" in kf.prompt.lower()
    assert "Voiceover" not in kf.prompt        # cleaned
    assert len(kf.prompt) < 900
    print(f"✅ keyframe prompt ({len(kf.prompt)} chars):\n   {kf.prompt[:120]}...")

    # Video
    vp = to_video_params(sample_scenario, sample_scene, keyframe_job_id="fake-kf-id")
    assert vp.model == "veo3_1"
    assert vp.duration == 4
    assert vp.image_ref == "fake-kf-id"
    assert "Voiceover" not in vp.prompt        # cleaned
    print(f"✅ video prompt ({len(vp.prompt)} chars):\n   {vp.prompt[:120]}...")

    # Policy — PASS
    result, reason = policy_prescreen(kf)
    assert result == PolicyResult.PASS, f"Expected PASS, got {result}: {reason}"
    print(f"✅ policy PASS")

    # Policy — FIXABLE (inject soft word — test conjugation "dies" is caught)
    soft_kf = KeyframeParams(prompt="The offer dies tonight, character at desk", aspect_ratio="9:16")
    result, reason = policy_prescreen(soft_kf)
    assert result == PolicyResult.FIXABLE, f"Expected FIXABLE, got {result}"
    print(f"✅ policy FIXABLE detected: {reason}")

    # Word-boundary: "skilled" must NOT trigger on "kill", "studies" must NOT trigger on "die"
    safe_kf = KeyframeParams(prompt="skilled professional studies the audience, whatever works", aspect_ratio="9:16")
    result, _ = policy_prescreen(safe_kf)
    assert result == PolicyResult.PASS, f"False positive! 'skilled/studies/whatever' triggered policy: {result}"
    print(f"✅ word-boundary false-positive guard: skilled/studies/whatever all PASS")

    # Policy — RANDOM (inject hard word)
    hard_kf = KeyframeParams(prompt="Character holds a gun in office", aspect_ratio="9:16")
    result, reason = policy_prescreen(hard_kf)
    assert result == PolicyResult.RANDOM, f"Expected RANDOM, got {result}"
    print(f"✅ policy RANDOM detected: {reason}")

    # Duration snap via SceneBlock
    long_scene = SceneBlock(label=SceneLabel.PROBLEM, time_range="3-15s", duration_s=12, description="x")
    assert long_scene.duration_s == 8
    vp2 = to_video_params(sample_scenario, long_scene, keyframe_job_id="x")
    assert vp2.duration == 8
    print(f"✅ duration snap: 12s → 8s")

    print("\n✅ translator.py — all checks passed")
