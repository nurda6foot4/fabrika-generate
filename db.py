"""
db.py — SQLite state tracker. No schema.py, no higgs.py imports.
Run standalone to verify: python3 db.py
"""

from __future__ import annotations
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "fabrika_generate.db"


# ── Connection ─────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    """Single writer. Never open two connections simultaneously."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")   # safe for reads during writes
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Schema init ────────────────────────────────────────────────────────────

def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id       TEXT PRIMARY KEY,
                scenario_id  TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clips (
                clip_id          TEXT PRIMARY KEY,
                job_id           TEXT NOT NULL REFERENCES jobs(job_id),
                scenario_id      TEXT NOT NULL,
                clip_index       INTEGER NOT NULL,
                scene_label      TEXT NOT NULL,
                duration_s       INTEGER NOT NULL,
                status           TEXT NOT NULL DEFAULT 'pending',
                retry_count      INTEGER NOT NULL DEFAULT 0,
                keyframe_job_id  TEXT,
                video_job_id     TEXT,
                file_path        TEXT,
                credits_keyframe REAL,
                credits_video    REAL,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS costs (
                cost_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id     TEXT NOT NULL REFERENCES jobs(job_id),
                clip_id    TEXT NOT NULL REFERENCES clips(clip_id),
                model      TEXT NOT NULL,
                credits    REAL NOT NULL,
                timestamp  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS errors (
                error_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id     TEXT NOT NULL REFERENCES clips(clip_id),
                stage       TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                timestamp   TEXT NOT NULL
            );
        """)


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Jobs ───────────────────────────────────────────────────────────────────

def create_job(job_id: str, scenario_id: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO jobs (job_id, scenario_id, status, created_at) VALUES (?,?,?,?)",
            (job_id, scenario_id, "pending", _now()),
        )

def update_job_status(job_id: str, status: str) -> None:
    with _conn() as con:
        con.execute("UPDATE jobs SET status=? WHERE job_id=?", (status, job_id))

def get_job(job_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None


# ── Clips ──────────────────────────────────────────────────────────────────

def create_clip(clip_id: str, job_id: str, scenario_id: str,
                clip_index: int, scene_label: str, duration_s: int) -> None:
    now = _now()
    with _conn() as con:
        con.execute(
            """INSERT INTO clips
               (clip_id, job_id, scenario_id, clip_index, scene_label,
                duration_s, status, retry_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,'pending',0,?,?)""",
            (clip_id, job_id, scenario_id, clip_index, scene_label, duration_s, now, now),
        )

def update_clip(clip_id: str, **fields) -> None:
    """Update any clip column by name. Always bumps updated_at."""
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [clip_id]
    with _conn() as con:
        con.execute(f"UPDATE clips SET {cols} WHERE clip_id=?", vals)

def get_clip(clip_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()
        return dict(row) if row else None

def get_resume_clip(job_id: str) -> dict | None:
    """Returns the first clip that is NOT done — safe resume point."""
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM clips
               WHERE job_id=? AND status NOT IN ('done','video_done')
               ORDER BY clip_index ASC LIMIT 1""",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

def get_all_clips(job_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM clips WHERE job_id=? ORDER BY clip_index ASC", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Costs ──────────────────────────────────────────────────────────────────

def log_cost(job_id: str, clip_id: str, model: str, credits: float) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO costs (job_id, clip_id, model, credits, timestamp) VALUES (?,?,?,?,?)",
            (job_id, clip_id, model, credits, _now()),
        )

def get_job_total_cost(job_id: str) -> float:
    with _conn() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(credits), 0) as total FROM costs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        return float(row["total"])


# ── Errors ─────────────────────────────────────────────────────────────────

def log_error(clip_id: str, stage: str, raw_payload: str | dict) -> None:
    """Store full raw CLI output or error dict — never just a status flag."""
    payload = json.dumps(raw_payload) if isinstance(raw_payload, dict) else raw_payload
    with _conn() as con:
        con.execute(
            "INSERT INTO errors (clip_id, stage, raw_payload, timestamp) VALUES (?,?,?,?)",
            (clip_id, stage, payload, _now()),
        )

def get_errors(clip_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM errors WHERE clip_id=? ORDER BY timestamp ASC", (clip_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Budget gate ────────────────────────────────────────────────────────────

class InsufficientCreditsError(Exception):
    pass

class BudgetCeilingError(Exception):
    pass

def check_budget(job_id: str, credits_needed: float,
                 current_balance: float, ceiling: float = 100.0) -> None:
    """
    Raises before any generation call.
    current_balance must be fetched fresh from higgs.get_balance() by caller.
    """
    if current_balance < credits_needed:
        raise InsufficientCreditsError(
            f"Balance {current_balance:.1f} < needed {credits_needed}"
        )
    spent = get_job_total_cost(job_id)
    if spent + credits_needed > ceiling:
        raise BudgetCeilingError(
            f"Job {job_id}: spent {spent:.1f} + needed {credits_needed} "
            f"exceeds ceiling {ceiling}"
        )


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    TEST_DB = Path(__file__).parent / "_test.db"
    if TEST_DB.exists():
        os.remove(TEST_DB)          # clean slate
    DB_PATH = TEST_DB               # override module-level var (_conn reads it at call time)

    try:
        init_db()

        # job
        create_job("job-001", "coursiv_001")
        assert get_job("job-001")["scenario_id"] == "coursiv_001"

        # clip
        create_clip("clip-001", "job-001", "coursiv_001", 0, "HOOK", 4)
        assert get_clip("clip-001")["status"] == "pending"

        # update clip
        update_clip("clip-001", status="keyframe_pending", keyframe_job_id="hf-abc")
        assert get_clip("clip-001")["status"] == "keyframe_pending"
        assert get_clip("clip-001")["keyframe_job_id"] == "hf-abc"

        # resume — clip-001 not done, so it should be returned
        resume = get_resume_clip("job-001")
        assert resume["clip_id"] == "clip-001"

        # mark done, resume should return None
        update_clip("clip-001", status="done")
        assert get_resume_clip("job-001") is None

        # costs
        log_cost("job-001", "clip-001", "nano_banana_2", 2.0)
        log_cost("job-001", "clip-001", "veo3_1", 11.0)
        assert get_job_total_cost("job-001") == 13.0

        # errors
        log_error("clip-001", "keyframe", {"error": "model timeout", "stdout": ""})
        errs = get_errors("clip-001")
        assert len(errs) == 1
        assert "model timeout" in errs[0]["raw_payload"]

        # budget gate — pass
        check_budget("job-001", credits_needed=11.0, current_balance=500.0, ceiling=100.0)

        # budget gate — ceiling exceeded (spent=13, needed=90 → 103 > 100)
        try:
            check_budget("job-001", credits_needed=90.0, current_balance=500.0, ceiling=100.0)
            assert False, "should have raised"
        except BudgetCeilingError as e:
            assert "ceiling" in str(e)

        # budget gate — insufficient balance
        try:
            check_budget("job-001", credits_needed=999.0, current_balance=5.0, ceiling=100.0)
            assert False, "should have raised"
        except InsufficientCreditsError as e:
            assert "Balance" in str(e)

        print("✅ db.py — all checks passed")
        print(f"   job created, clip CRUD, resume logic, costs, errors, budget gate")

    finally:
        if TEST_DB.exists():
            os.remove(TEST_DB)
