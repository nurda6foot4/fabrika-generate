"""
higgs.py — Higgsfield CLI wrapper. No schema.py, no db.py imports.
All calls go through subprocess → higgsfield CLI → JSON parsed back.
Run standalone to verify: python3 higgs.py  (costs 0 credits)
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path

CLI = "higgsfield"   # must be on PATH; validated in self-test


# ── Internal runner ────────────────────────────────────────────────────────

class HiggsError(Exception):
    """Raised when CLI returns non-zero or unparseable output."""
    def __init__(self, msg: str, stdout: str = "", stderr: str = ""):
        super().__init__(msg)
        self.stdout = stdout
        self.stderr = stderr
        self.raw = {"stdout": stdout, "stderr": stderr, "msg": msg}

def _run(*args: str, timeout: int = 600) -> dict:
    """Run higgsfield CLI with --json flag. Returns parsed dict."""
    cmd = [CLI, *args, "--json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise HiggsError(
            f"CLI exit {result.returncode}: {result.stderr.strip()[:200]}",
            stdout=result.stdout,
            stderr=result.stderr,
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise HiggsError(
            f"JSON parse failed: {e}",
            stdout=result.stdout,
            stderr=result.stderr,
        )


# ── Account ────────────────────────────────────────────────────────────────

def get_balance() -> float:
    """Returns current credit balance. Costs nothing."""
    data = _run("account", "status")
    return float(data["credits"])

def get_account() -> dict:
    """Returns full account info: email, credits, plan."""
    return _run("account", "status")


# ── Cost estimation ────────────────────────────────────────────────────────

def estimate_cost(model: str, prompt: str, **params) -> float:
    """
    Returns estimated credits for a generation. Costs nothing.
    Extra params: duration=6, aspect_ratio="9:16", etc.
    """
    args = ["generate", "cost", model, "--prompt", prompt]
    for k, v in params.items():
        args += [f"--{k}", str(v)]        # keep underscores
    data = _run(*args)
    return float(data.get("credits", data.get("credits_exact", 0)))


# ── Generation ─────────────────────────────────────────────────────────────

def generate_image(model: str, prompt: str,
                   aspect_ratio: str = "9:16", **extra) -> str:
    """
    Creates an image generation job. Returns job_id.
    Does NOT wait — call wait_job() separately if needed.
    For nano_banana_2: costs 2 credits.
    """
    args = [
        "generate", "create", model,
        "--prompt", prompt,
        "--aspect_ratio", aspect_ratio,   # model params use underscore, not hyphen
    ]
    for k, v in extra.items():
        args += [f"--{k}", str(v)]        # keep underscores — CLI expects param names as-is
    data = _run(*args)
    # CLI returns either ["job-id"] list or {"id": "job-id"} dict
    if isinstance(data, list):
        job_id = data[0] if data else None
    else:
        job_id = data.get("id") or data.get("job_id") or data.get("jobId")
    if not job_id:
        raise HiggsError("No job_id in response", stdout=json.dumps(data))
    return str(job_id)

def generate_video(model: str, prompt: str,
                   image_ref: str, duration: int,
                   aspect_ratio: str = "9:16", **extra) -> str:
    """
    Creates a video generation job using image_ref as start_image.
    Returns job_id. Does NOT wait.
    For veo3_1: 4s=11cr, 6s=16.5cr, 8s=22cr.
    """
    args = [
        "generate", "create", model,
        "--prompt", prompt,
        "--image", image_ref,              # veo3_1 uses --image (same as nano_banana_2)
        "--duration", str(duration),
        "--aspect_ratio", aspect_ratio,   # model param — underscore
    ]
    for k, v in extra.items():
        args += [f"--{k}", str(v)]        # keep underscores
    data = _run(*args)
    # CLI returns either ["job-id"] list or {"id": "job-id"} dict
    if isinstance(data, list):
        job_id = data[0] if data else None
    else:
        job_id = data.get("id") or data.get("job_id") or data.get("jobId")
    if not job_id:
        raise HiggsError("No job_id in response", stdout=json.dumps(data))
    return str(job_id)


# ── Job polling ────────────────────────────────────────────────────────────

def wait_job(job_id: str, timeout_minutes: int = 15) -> dict:
    """
    Blocks until job completes. Returns full job dict including result URLs.
    Uses CLI's built-in polling — no sleep loop needed here.
    """
    timeout_arg = f"{timeout_minutes}m"
    data = _run(
        "generate", "wait", job_id,
        "--timeout", timeout_arg,
        "--interval", "5s",
        timeout=timeout_minutes * 60 + 30,
    )
    return data

def get_job(job_id: str) -> dict:
    """Returns current job status without waiting."""
    return _run("generate", "get", job_id)

def get_result_urls(job_result: dict) -> list[str]:
    """
    Extracts output URLs from a completed job dict.
    Handles different response shapes from the CLI.
    """
    urls: list[str] = []

    # shape 1: result_url (confirmed from nano_banana_2 / veo3_1 live responses)
    if job_result.get("result_url"):
        urls.append(job_result["result_url"])

    # shape 2: results[].url
    for item in job_result.get("results", []):
        if isinstance(item, dict) and item.get("url"):
            urls.append(item["url"])

    # shape 3: outputs[].url
    for item in job_result.get("outputs", []):
        if isinstance(item, dict) and item.get("url"):
            urls.append(item["url"])

    # shape 4: direct url field
    if not urls and job_result.get("url"):
        urls.append(job_result["url"])

    return urls


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil

    # verify CLI is on PATH
    if not shutil.which(CLI):
        raise SystemExit(f"❌ '{CLI}' not found on PATH. Check npm-global installation.")

    print("Testing get_balance() — costs 0 credits...")
    balance = get_balance()
    assert isinstance(balance, float), f"Expected float, got {type(balance)}"
    assert balance > 0, f"Balance is {balance} — unexpected"
    print(f"✅ higgs.py — get_balance() = {balance:.2f} credits")

    print("Testing estimate_cost(nano_banana_2)...")
    cost = estimate_cost("nano_banana_2", prompt="test scene")
    assert cost == 2.0, f"Expected 2.0, got {cost}"
    print(f"✅ estimate_cost nano_banana_2 = {cost} credits")

    print("Testing estimate_cost(veo3_1, 4s)...")
    cost_veo = estimate_cost("veo3_1", prompt="test scene", duration=4)
    assert cost_veo == 11.0, f"Expected 11.0, got {cost_veo}"
    print(f"✅ estimate_cost veo3_1 4s = {cost_veo} credits")

    print("\n✅ higgs.py — all checks passed (0 credits spent)")
