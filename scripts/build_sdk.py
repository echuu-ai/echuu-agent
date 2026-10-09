"""Build immutable SDK artifacts from a clean, complete Git checkout."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

BASE = "72f50d81ff22693d5389b6b2705b58e71657fcc6"
TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def describe(root: Path) -> dict[str, str | int]:
    if git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise ValueError("Fetch complete Git history before building")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Commit all source changes before building")
    commit = git(root, "rev-parse", "HEAD")
    tags = git(root, "tag", "--merged", "HEAD").splitlines()
    candidates = []
    for tag in tags:
        match = TAG.fullmatch(tag)
        if match:
            distance = int(git(root, "rev-list", "--count", f"{tag}..HEAD"))
            candidates.append((distance, tuple(map(int, match.groups())), tag))
    if candidates:
        distance, release, tag = min(
            candidates, key=lambda row: (row[0], tuple(-n for n in row[1]))
        )
        base = git(root, "rev-parse", f"{tag}^{{commit}}")
        version = ".".join(map(str, release))
        if distance:
            major, minor, patch = release
            version = f"{major}.{minor}.{patch + 1}.dev{distance}+g{commit[:12]}"
    else:
        subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", BASE, "HEAD"],
            check=True,
        )
        base, tag = BASE, ""
        distance = int(git(root, "rev-list", "--count", f"{base}..HEAD"))
        version = f"0.2.0.dev{distance}+g{commit[:12]}"
    return {
        "version": version,
        "source_commit": commit,
        "base_commit": base,
        "base_tag": tag,
        "distance": distance,
        "schema_version": 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    provenance = describe(root)
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error(
            "Output directory must be empty; do not overwrite published artifacts"
        )
    with tempfile.TemporaryDirectory(prefix="echuu-sdk-build-") as directory:
        staging = Path(directory)
        archive = staging / "source.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar",
                "HEAD",
                "-o",
                str(archive),
            ],
            check=True,
        )
        subprocess.run(["tar", "-xf", str(archive), "-C", str(staging)], check=True)
        (staging / "echuu" / "_version.py").write_text(
            f"__version__ = {provenance['version']!r}\n"
        )
        (staging / "echuu" / "_build.json").write_text(
            json.dumps(provenance, indent=2) + "\n"
        )
        subprocess.run(
            ["uv", "build", str(staging), "--out-dir", str(output)], check=True
        )
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
