"""Inspect an installed SDK from outside the checkout, without provider calls.

Run with a clean environment containing the built wheel, pytest and pytest-asyncio.
Artifacts include exact commands, logs, dependency versions and event samples.
Exit 1 means a regression or packaging blocker was observed; no xfails are added.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTS = (
    "test_background_interactions.py",
    "test_topic_evolution.py",
    "test_danmaku_interleave.py",
    "test_engine_unit_render.py",
    "test_preparation.py",
    "test_prepared_speech.py",
    "test_pause_after.py",
    "test_step_event_schema.py",
    "test_sdk_configuration.py",
    "test_live_session.py",
    "test_session_extensions.py",
    "test_gift_runtime.py",
)

PROBE = r"""
import contextlib
import importlib.metadata
import inspect
import io
import json
from pathlib import Path
import random
import socket
import sys

def deny_network(*args, **kwargs):
    raise RuntimeError("Baseline forbids network access")

socket.socket.connect = deny_network
socket.create_connection = deny_network
import echuu
from echuu.live.engine import EchuuLiveEngine
from echuu.live.tts_client import TTSClient

package = Path(echuu.__file__).resolve()
assert package.is_relative_to(Path(sys.prefix).resolve()), package
with contextlib.redirect_stdout(io.StringIO()) as captured:
    tts_class = TTSClient.__new__(TTSClient)._load_cosyvoice_class()
report = {
    "python": sys.version,
    "package_path": str(package),
    "distribution_version": importlib.metadata.version("echuu"),
    "tts_implementation_available": tts_class is not None,
    "tts_loader_output": captured.getvalue(),
    "run_signature": str(inspect.signature(EchuuLiveEngine.run)),
    "package_example_valid": "run_sync(" not in (echuu.__doc__ or "") or hasattr(EchuuLiveEngine, "run_sync"),
    "dependencies": sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()),
}
# Only copied test helpers are added, never the source package or Web backend.
sys.path.insert(0, str(Path.cwd()))
from tests.test_background_interactions import rig
from tests.test_danmaku_interleave import _engine_with, _make_show
from echuu.harness.runtime import serializable
random.seed(42)
engine = _engine_with(_make_show(), [])
with contextlib.redirect_stdout(io.StringIO()):
    normal = list(engine.run(max_steps=1))
samples = {"normal": serializable(normal)}
for scenario in ("reply", "timeout", "stop"):
    engine, background, job, clock, events = rig()
    job.dm.id = "baseline-interaction"
    try:
        if scenario == "reply":
            job.reply.set_result({"speech": "这个主意挺暖心的。", "audio": b"fixture-audio"})
            reply = background.boundary(0)
            samples["reply_step"] = serializable(reply)
        elif scenario == "timeout":
            job.rewrite.set_running_or_notify_cancel()
            clock[0] = 25.1
            background.boundary(0)
        else:
            background.close()
        samples[scenario] = serializable(events)
    finally:
        background.close()
Path("event-samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n")
Path("probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "dependencies"}, ensure_ascii=False))
"""

NETWORK_GUARD = """
import socket
import pytest

@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def deny(*args, **kwargs):
        raise RuntimeError("Baseline forbids network access")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source-regression",
        action="store_true",
        help="Compare source tests including REST/WS; requires Web dependencies",
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error("output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    tests = output / "tests"
    tests.mkdir()
    for name in ("__init__.py", *TESTS):
        shutil.copyfile(source / "tests" / name, tests / name)
    (tests / "conftest.py").write_text(NETWORK_GUARD)
    # Never inherit provider credentials, PYTHONPATH, or implicit dotenv loading.
    env = {"PATH": os.defpath, "PYTHON_DOTENV_DISABLED": "1", "PYTHONUTF8": "1"}
    commands = [
        [sys.executable, "-I", "-c", PROBE],
        [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "-q",
            "-o",
            "asyncio_mode=auto",
            "tests",
        ],
    ]
    if args.source_regression:
        selected = [
            str(source / "tests" / name)
            for name in (
                *TESTS,
                "test_topic_evolution_api.py",
                "test_sdk_legacy_import.py",
            )
        ]
        runner = (
            "import sys, socket\n"
            f"sys.path.insert(0, {str(source)!r})\n"
            "def deny(*args, **kwargs):\n"
            "    raise RuntimeError('Baseline forbids network access')\n"
            "socket.socket.connect = deny\n"
            "socket.create_connection = deny\n"
            "import pytest\n"
            f"raise SystemExit(pytest.main({['-q', '-o', 'asyncio_mode=auto', *selected]!r}))\n"
        )
        commands[1] = [sys.executable, "-I", "-c", runner]
    results = []
    for name, command in zip(("probe", "regression"), commands):
        with (output / f"{name}.log").open("w") as log:
            try:
                result = subprocess.run(
                    command,
                    cwd=output,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                    check=False,
                )
                status = result.returncode
            except subprocess.TimeoutExpired:
                log.write("\nBaseline command exceeded 180 seconds\n")
                status = 124
        results.append({"name": name, "command": command, "exit_code": status})
        print(f"{name}: exit={status}; log={output / (name + '.log')}")
    probe = (
        json.loads((output / "probe.json").read_text())
        if (output / "probe.json").exists()
        else {}
    )
    blockers = []
    if not probe.get("tts_implementation_available"):
        blockers.append("Installed wheel cannot load the bundled TTS implementation")
    if not probe.get("package_example_valid"):
        blockers.append("Public package example calls missing run_sync()")
    report = {
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip(),
        "results": results,
        "blockers": blockers,
        "source_status": subprocess.check_output(
            ["git", "status", "--short"], cwd=source, text=True
        ),
        "scope": (
            "Installed probe plus source REST/WS regression"
            if args.source_regression
            else "Installed wheel regression"
        )
        + "; deterministic doubles; no real LLM/TTS",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report["blockers"], ensure_ascii=False))
    return int(bool(blockers) or any(r["exit_code"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
