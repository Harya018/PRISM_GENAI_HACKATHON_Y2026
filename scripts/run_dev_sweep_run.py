#!/usr/bin/env python3
"""Drives ONE sweep run against the isolated 25-recording dev subset
(Full-Duplex-Bench/v3/dev_subset_data/, built from results/dev_subset.txt's folder names):
starts the agent with the given env vars, smoke-tests on 4 recordings, runs the real 25-example
inference, stops the agent, scores, evaluates via the UNMODIFIED official evaluate_tool_calls.py
/ evaluate_pass_rate.py (Rule 6 -- never touches scoring logic, just invokes it against an
isolated results-dir so there is no scenario-id ambiguity to filter around), then appends a row
to results/ABLATIONS.md using the official aggregate directly.

Quota hygiene (all per the task's Rule 5): one agent process at a time, restarted for every run
(each run's smoke test + 25 examples is well under a 25-example restart cadence), 4-example
smoke test before the real batch, abort if the smoke test comes back 3+ consecutive empty.

Usage:
  python scripts/run_dev_sweep_run.py --provider dev_r1 --label "baseline-A" \
      [--env LK_THINKING_BUDGET=0] [--env LK_KEY_INFO_FIRST=1] ...
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_subset_eval  # noqa: E402 -- shares the evaluate+append-row implementation

FDB_ROOT = Path(__file__).resolve().parent.parent.parent / "Full-Duplex-Bench" / "v3"
DEV_DATA_DIR = FDB_ROOT / "dev_subset_data"
VENV_PY = Path(__file__).resolve().parent.parent.parent / "fdb-venv" / "Scripts" / "python.exe"

FFMPEG_BIN = (r"C:\Users\harya\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_"
             r"Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin")


def _env(extra: dict) -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = FFMPEG_BIN + os.pathsep + env.get("PATH", "")
    env["LK_PROVIDER"] = "gemini2_5"
    env.update(extra)
    return env


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _has_content(result_path: Path) -> bool:
    """Post-SCORING check: actual_tool_calls/transcript only exist after --asr-only has run."""
    if not result_path.exists():
        return False
    try:
        d = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(d.get("actual_tool_calls")) or bool((d.get("transcript") or "").strip())


def _infer_succeeded(result_path: Path) -> bool:
    """Post-INFERENCE (--infer-only) check, used by the smoke test: at this stage the result
    file has no actual_tool_calls/transcript yet (those are populated by scoring) -- the real
    signal that inference actually produced audio is status == "awaiting_scoring". Found via a
    real bug: the smoke test originally checked actual_tool_calls/transcript and reported 0/4
    "empty" even though the tool's own log showed 4/4 successful inferences -- it was checking
    fields that don't exist until after a scoring pass the smoke test never runs."""
    if not result_path.exists():
        return False
    try:
        d = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return d.get("status") == "awaiting_scoring"


def _clear_status(data_dir: Path, provider: str) -> None:
    for rf in data_dir.glob(f"*/result_{provider}.json"):
        rf.unlink(missing_ok=True)
        (rf.parent / f"output_{provider}.wav").unlink(missing_ok=True)


def start_agent(env_extra: dict):
    log_path = FDB_ROOT / f"agent_devsweep.log"
    err_path = FDB_ROOT / f"agent_devsweep.err.log"
    log_f = open(log_path, "w", encoding="utf-8")
    err_f = open(err_path, "w", encoding="utf-8")
    proc = subprocess.Popen([str(VENV_PY), "lk_agent_ours.py", "start"], cwd=str(FDB_ROOT),
                            env=_env(env_extra), stdout=log_f, stderr=err_f)
    _log(f"agent started, pid={proc.pid}")
    deadline = time.time() + 60
    registered = False
    while time.time() < deadline:
        if log_path.exists() and '"registered worker"' in log_path.read_text(encoding="utf-8", errors="ignore"):
            registered = True
            break
        time.sleep(2)
    if not registered:
        _log("WARNING: agent did not confirm registration within 60s")
    return proc, log_f, err_f


def stop_agent(proc, log_f, err_f):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    log_f.close()
    err_f.close()
    _log("agent stopped")


def run_infer(data_dir: Path, provider: str) -> None:
    cmd = [str(VENV_PY), "run_tool_benchmark_all_released.py", "--provider", provider,
          "--root_dir", str(data_dir), "--infer-only", "--agent-name", "fdb-ours"]
    # Deliberately NOT capturing output: capturing+decoding it in THIS process is what crashed
    # R1's smoke test (Windows console cp1252 decode on the script's own emoji prints, in a
    # background reader thread, silently losing the actual subprocess result). Letting
    # stdout/stderr flow through uncaptured avoids the decode step entirely -- same pattern
    # overnight_runner.py used reliably all session (redirect to a file, never decode in-process).
    subprocess.run(cmd, cwd=str(FDB_ROOT), env=_env({}))


def run_score(data_dir: Path, provider: str) -> None:
    cmd = [str(VENV_PY), "run_tool_benchmark_all_released.py", "--provider", provider,
          "--root_dir", str(data_dir), "--asr-only"]
    subprocess.run(cmd, cwd=str(FDB_ROOT), env=_env({}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, help="unique provider name for this run, e.g. dev_r1")
    ap.add_argument("--label", required=True, help="ABLATIONS.md row label")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE, repeatable")
    args = ap.parse_args()

    env_extra = {}
    for kv in args.env:
        k, _, v = kv.partition("=")
        env_extra[k] = v
    _log(f"run {args.label!r} provider={args.provider} env={env_extra}")

    _clear_status(DEV_DATA_DIR, args.provider)

    proc, log_f, err_f = start_agent(env_extra)
    try:
        # smoke test: first 4 dev-subset folders only, via a throwaway 4-folder copy so a bad
        # smoke test can't leave partial results mixed into the real 25-example run.
        smoke_dir = FDB_ROOT / "dev_subset_smoke4"
        if smoke_dir.exists():
            import shutil
            shutil.rmtree(smoke_dir)
        smoke_dir.mkdir()
        import shutil
        dev_folders = sorted(d.name for d in DEV_DATA_DIR.iterdir() if d.is_dir())[:4]
        for name in dev_folders:
            shutil.copytree(DEV_DATA_DIR / name, smoke_dir / name)

        _log("running 4-example smoke test...")
        run_infer(smoke_dir, args.provider)
        smoke_hits = [_infer_succeeded(smoke_dir / name / f"result_{args.provider}.json")
                     for name in dev_folders]
        _log(f"smoke test: {sum(smoke_hits)}/4 non-empty ({smoke_hits})")
        # abort on 3+ CONSECUTIVE empties anywhere in the 4, per the quota-hygiene rule -- not
        # just "all empty", so an early bad streak still aborts even if the 4th happens to hit.
        consecutive_empty = 0
        for hit in smoke_hits:
            consecutive_empty = 0 if hit else consecutive_empty + 1
            if consecutive_empty >= 3:
                _log("ABORT: 3+ consecutive empty smoke-test results -- not spending quota on "
                    "the real batch")
                return 1

        _log("running full 25-example inference...")
        run_infer(DEV_DATA_DIR, args.provider)
    finally:
        stop_agent(proc, log_f, err_f)

    _log("scoring (NeMo ASR, no agent needed)...")
    run_score(DEV_DATA_DIR, args.provider)

    n_content = sum(1 for d in DEV_DATA_DIR.iterdir() if d.is_dir()
                    and _has_content(d / f"result_{args.provider}.json"))
    _log(f"{n_content}/25 recordings have real content after inference+scoring")

    _log("evaluating (official, unmodified evaluate_tool_calls.py / evaluate_pass_rate.py)...")
    tc, pr = dev_subset_eval.evaluate_provider(args.provider)
    row = dev_subset_eval.append_ablation_row(args.label, tc, pr)

    _log("Appended row:")
    print(row.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
