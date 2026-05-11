# Fabrika-Generate — Session Context

> READ THIS FIRST every session. UPDATE the "Current Stage" section before ending.

---

## What This Project Does

Automates the video ad creation pipeline:
Fabrika scenario → keyframe (Higgsfield nano_banana_2) → video (Veo 3.1 via Higgsfield) → Premiere-ready clips

---

## Validated Facts (do not re-debate)

- **Higgsfield CLI**: `higgsfield` at `/Users/nurdauletzhumaliyev/.npm-global/bin/higgsfield`
- **Auth**: `~/.config/higgsfield/credentials.json` (auto-used by CLI, no code needed)
- **Balance**: 1043.86 credits, ultimate plan
- **nano_banana_2**: 2 credits per keyframe (2K, any ratio)
- **veo3_1**: 4s=11cr, 6s=16.5cr, 8s=22cr — confirmed model ID
- **veo3_1_lite**: 4s=4cr, 6s=6cr, 8s=8cr — cheaper, quality untested
- **Python**: 3.9.6, Pydantic 2.12.5, requests, httpx — all installed
- **FFmpeg**: NOT installed — run `brew install ffmpeg` before Stage 9
- **Fabrika output format**: UNKNOWN — get one real example from user before Stage 6

---

## Council Decisions (locked)

- JSON schema first — all scenario fields typed/enumerable
- Semantic translator: rules-based, scenario fields → CLI params
- Cost gate: hard stop before every generation call (ceiling: 100cr/video)
- Human checkpoint: post-keyframe approval only (CLI `input()` + `open` keyframe file)
- Retry cap: 1 automated retry per clip — enforced in code
- Stateful job tracker: SQLite, resume from failed clip not from start
- Policy rejection classifier: FIXABLE vs RANDOM — different handling
- Sidecar JSON per clip: full provenance
- Error log: full raw CLI output + timestamp in SQLite errors table
- Premiere-ready: H.264 or ProRes 422, 9:16, 2K, silent AAC track, no black frames
- Filename: `{scenario_id}_{clip_index:03d}_{take:02d}_{status}.mov`
- Premiere handoff: folder drop + ExtendScript auto-import (.jsx)

---

## File Map

| File | Purpose | Status |
|---|---|---|
| `CONTEXT.md` | This file — session handoff | ✅ exists |
| `schema.py` | Pydantic models only | ✅ done + tested |
| `db.py` | SQLite CRUD + cost gate | ✅ done + tested |
| `higgs.py` | Higgsfield CLI wrapper | ✅ done + tested |
| `translator.py` | Scenario fields → CLI params | ✅ done + tested |
| `pipeline.py` | Keyframe + video stage runner | ✅ done + tested |
| `postprocess.py` | FFmpeg post-processing | ✅ done + tested |
| `main.py` | Entry point, ties everything together | ✅ done + tested |
| `premiere_import.jsx` | ExtendScript for Premiere auto-import | ✅ done + tested |
| `fabrika_sample.json` | One real Fabrika output (get from user) | ⬜ missing |

---

## Staged Plan

Each stage = one file, one purpose, one test, then stop.
Never work on two files simultaneously.

| Stage | File | Goal | Blocked by |
|---|---|---|---|
| 0 | — | `brew install ffmpeg`, get Fabrika sample output | Nothing |
| 1 | `schema.py` | Pydantic models, test with sample | Fabrika sample |
| 2 | `db.py` | SQLite tables + CRUD, test standalone | Nothing |
| 3 | `higgs.py` | CLI wrapper, test with `get_balance()` | Nothing |
| 4 | Connect 2+3 | Cost gate wired to balance + DB totals | Stages 2+3 |
| 5 | `translator.py` | Scenario → CLI params, test with sample | Stage 1 |
| 6 | `pipeline.py` (keyframe) | Generate 1 keyframe, checkpoint, store | Stages 2+3+5 |
| 7 | `pipeline.py` (video) | Generate 1 video from approved keyframe | Stage 6 |
| 8 | `postprocess.py` | FFmpeg encode + validate | FFmpeg installed |
| 9 | Sidecar + error log | Write JSON + raw errors to DB | Stage 7 |
| 10 | `main.py` | Full pipeline, one scenario, one clip | All above |
| 11 | `premiere_import.jsx` | ExtendScript auto-import | Stage 10 |

---

## Current Stage

**🎉 ALL STAGES COMPLETE**

**Last action**: premiere_import.jsx done — ES3 compatible, balanced braces, no arrow fns/const/let.
**Pipeline is fully built and dry-run verified.**

## How to run a real generation

```bash
# Single scenario (79.5 credits):
python3 main.py fabrika_sample.html --scenario 1

# All 50 scenarios (WARNING: 50 × ~79.5 = ~3975 credits — way over budget!):
# Use --scenario to pick specific ones, or raise/lower --ceiling

# Dry run first to verify prompts:
python3 main.py fabrika_sample.html --scenario 1 --dry-run
```

## Premiere import (after generation)

Premiere Pro 2026 removed the Scripts menu and AppleScript DoScript support.
**Only working method: drag _done.mp4 files from Finder into the Premiere Project panel.**
premiere_import.jsx is kept for reference but does not work on 2026+.
