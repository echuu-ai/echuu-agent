import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "build_sdk", Path(__file__).parents[1] / "scripts/build_sdk.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def command(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_version_tracks_committed_source_and_tags(tmp_path, monkeypatch):
    command(tmp_path, "init", "-q")
    command(tmp_path, "config", "user.name", "SDK Test")
    command(tmp_path, "config", "user.email", "sdk@example.invalid")
    source = tmp_path / "source.py"
    source.write_text("initial\n")
    command(tmp_path, "add", ".")
    command(tmp_path, "commit", "-qm", "initial")
    base = command(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setattr(build, "BASE", base)
    assert build.describe(tmp_path)["version"] == f"0.2.0.dev0+g{base[:12]}"
    source.write_text("changed\n")
    with pytest.raises(ValueError, match="Commit all"):
        build.describe(tmp_path)
    command(tmp_path, "add", ".")
    command(tmp_path, "commit", "-qm", "changed")
    commit = command(tmp_path, "rev-parse", "HEAD")
    result = build.describe(tmp_path)
    assert result == build.describe(tmp_path)
    assert result["version"] == f"0.2.0.dev1+g{commit[:12]}"
    assert result["source_commit"] == commit
    command(tmp_path, "tag", "v0.2.0")
    assert build.describe(tmp_path)["version"] == "0.2.0"
    source.write_text("next\n")
    command(tmp_path, "add", ".")
    command(tmp_path, "commit", "-qm", "next")
    assert build.describe(tmp_path)["version"].startswith("0.2.1.dev1+g")
    (tmp_path / "untracked.py").write_text("new\n")
    with pytest.raises(ValueError, match="Commit all"):
        build.describe(tmp_path)
