# Fabrika-Generate

**Turn a Higgsfield creative brief into 16 ready-to-air video clips — fully automated.**

Feed the system an HTML report from Higgsfield's Fabrika planning tool. It generates two distinct characters, writes scene-by-scene visual prompts, produces a keyframe per clip, animates each one into a video, encodes everything to H.264 1080×1920, and packages the result for Adobe Premiere — no human intervention required.

Built as a technical project to apply for Higgsfield. Real output, real credits, real client brief.

---

## What it does

```
fabrika_sample.html  →  2 character portraits
                     →  16 keyframes (nano_banana_2)
                     →  16 video clips (veo3_1)
                     →  16 Premiere-ready .mp4 files + JSON sidecars
```

One clean run costs **256 credits** and takes about **67 minutes** end-to-end.

---

## Why this was hard to build

### Budget constraint — only 1,200 promo credits
No room for sloppy prompts. Every wrong generation wasted real money.  
**Solution:** Used [RUFLO](https://github.com/ruvnet/ruflo) — an open-source LLM orchestrator — to route tasks to the right model at the right time, and the **LLM Council skill** to cross-check prompts before they hit the API. This caught bad generations before credits were spent.

### Claude $20/month context limits
Long pipelines hit context walls mid-run, dropping state.  
**Solution:** All job state lives in SQLite. Every run is resumable from the exact clip it failed on — nothing restarts from scratch.

### Split-screen keyframes
The image model kept returning 4-panel character sheets instead of single scenes.  
**Solution:** Added explicit negative prompts (`no panels, no split screen, no collage, single continuous scene`) and rewrote the character reference workflow to generate single-portrait refs.

### Both characters looked identical
Person A and Person B both came out as the same man. The system used the same description for both.  
**Solution:** Built a character library per `TalentFormat`. Person B became a woman with distinct clothing, hair, and framing — visually unmistakeable from Person A.

### Dialogue scenes broke the speaker assignment
Lines like `Person A: 'text'` were silently dropped by the parser, so the character ref selection had nothing to go on.  
**Solution:** Wrote a dialogue expander that extracts each speaker block via regex, splits long speeches into 8-second clips, and tags each clip with who is speaking so ref injection is automatic.

### Pipeline crashed unattended
The keyframe approval checkpoint called `input()` — fatal in batch mode.  
**Solution:** Added `--auto-approve` flag to skip it entirely when running non-interactively.

---

## How the pipeline works

```
1. Parse HTML brief          — BeautifulSoup extracts 50 scenarios; you pick one by rank
2. Generate character refs   — Two portraits via nano_banana_2, saved as job IDs
3. Keyframe per clip         — Visual prompt built per scene; correct character ref injected
4. Human checkpoint          — Opens keyframe image, asks y/n (skip with --auto-approve)
5. Animate to video          — veo3_1 animates keyframe with motion prompt + voiceover text
6. Encode & validate         — FFmpeg → H.264 1080×1920, codec/resolution verified
7. Sidecar JSON              — Full provenance: prompts, job IDs, credits, ffprobe metadata
```

---

## File map

| File | What it does |
|------|-------------|
| `schema.py` | Pydantic models — `ScenarioInput`, `SceneBlock`, `ClipJob`, `VideoParams` |
| `db.py` | SQLite CRUD — job/clip/cost tracking, budget ceiling gate, resume logic |
| `higgs.py` | Higgsfield CLI wrapper — all API calls go through subprocess → JSON |
| `translator.py` | Converts scenario fields into image/video prompt strings |
| `character.py` | Generates character reference sheets; picks A or B ref per clip |
| `pipeline.py` | Runs keyframe → approval → video for one clip; handles retries |
| `postprocess.py` | FFmpeg encode, resolution validation, sidecar JSON writer |
| `main.py` | Entry point — parses HTML, orchestrates the full pipeline |
| `run_videos.py` | Resume script — skips keyframes, runs only the video phase |
| `premiere_import.jsx` | ExtendScript — auto-imports all clips into Adobe Premiere |

---

## Requirements

- Python 3.9+
- [Higgsfield CLI](https://higgsfield.ai) — authenticated (`higgsfield account status`)
- FFmpeg — `brew install ffmpeg`
- Pydantic 2.x — `pip install pydantic`
- BeautifulSoup — `pip install beautifulsoup4`

---

## Quick start

```bash
# 0 credits — verify everything parses correctly
python3 main.py fabrika_sample.html --scenario 1 --dry-run

# Live run — one scenario (~256 credits for a 16-clip dialogue format)
python3 main.py fabrika_sample.html --scenario 1

# Skip keyframe approval checkpoint (batch mode)
python3 main.py fabrika_sample.html --scenario 1 --auto-approve

# Budget ceiling (default 100cr, raise for full runs)
python3 main.py fabrika_sample.html --scenario 1 --ceiling 300

# Resume only the video phase (keyframes already approved in DB)
python3 run_videos.py <job_id> fabrika_sample.html --scenario 1 --ceiling 300
```

Output lands in `output/<job_id>/` as `{scenario_id}_{clip_index:03d}_01_done.mp4`.

---

## Credit costs (Higgsfield)

| Step | Model | Cost |
|------|-------|------|
| Keyframe | nano_banana_2 | 2cr each |
| Video 4s | veo3_1 | 11cr |
| Video 6s | veo3_1 | 16.5cr |
| Video 8s | veo3_1 | 22cr |
| **Full 16-clip run** | mixed | **~256cr** |

---

## Built with

- **[Higgsfield API](https://higgsfield.ai)** — image generation (nano_banana_2) + video generation (veo3_1)
- **[RUFLO](https://github.com/ruvnet/ruflo)** — open-source LLM orchestrator; used to route tasks and run LLM Council cross-checks to reduce wasted API calls under a tight credit budget
- **Python** — pipeline orchestration, HTML parsing, prompt engineering
- **SQLite** — stateful job tracker with full resume capability
- **FFmpeg** — encode, validate, package
- **Claude Code** — agentic coding assistant used throughout to build and debug

---

## Real-world context

The brief used (`fabrika_sample.html`) is a validated Higgsfield Fabrika scenario pack for **Coursiv** — an AI education product.  
The output feeds directly into **Zimran's** Meta ad creative testing workflow: a company that actively spends on Meta campaigns and uses Fabrika scenarios to check hypotheses before scaling spend.

---

## Author

**Nurdaulet Zhumaliyev** — rmeyramuly@gmail.com  
[Landing page](https://nurdauletzhumaliyev.github.io/fabrika-generate) · Built for Higgsfield job application
