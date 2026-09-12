"""Lock down the "示例内容泄漏" surface described in
docs/research/2026-09-11-fewshot-leakage-retest.md.

`TriggerBank` (trigger_bank.py), `DramaAmplifier` (drama_amplifier.py),
`StoryNucleus` (story_nucleus.py) and `DigressionDB` (digression_db.py, the
templated-chain class — not the two free functions `topic_return_hook` /
`scrub_recited_topic` that `output_safety.py` legitimately uses) previously
carried hardcoded example content copied from one training clip
(海苔/腰果/偷吃 cashews-under-seaweed story; 汇率/七块/四块八 currency guesses).

None of these four are wired into the real live-broadcast generation path
today (verified by reading `echuu/live/*.py` and `echuu/generators/*.py`:
their only reachable references are `echuu/__init__.py` and
`echuu/core/__init__.py` package re-exports, which every module picks up
for free simply by importing anything under `echuu` — that is *not* the
same as being called from the generation pipeline).

Two independent guards, so a future "let's reconnect this for flavor" PR
can't silently reintroduce canned experiences into what the streamer says:

1. Content lock — the banned literal strings must never exist anywhere
   under `echuu/`, whether the module is wired in or not.
2. Usage lock — nothing outside the four modules' own files (and their
   package `__init__.py` re-exports, and test doubles) may construct or
   call these symbols. If a future change wires one in, this test fails
   and forces a deliberate, reviewed update here instead of a silent leak.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ECHUU_SRC = REPO_ROOT / "echuu"

_BANNED_LITERALS = ("海苔", "腰果", "汇率", "七块", "四块八")

# Files allowed to *define* the leak-prone symbols.
_DEFINING_FILES = {
    ECHUU_SRC / "core" / "trigger_bank.py",
    ECHUU_SRC / "core" / "drama_amplifier.py",
    ECHUU_SRC / "core" / "story_nucleus.py",
    ECHUU_SRC / "core" / "digression_db.py",
}
# Package re-export points; importing *anything* under echuu pulls these in
# for free, which is not the same as the live pipeline calling them.
_REEXPORT_FILES = {
    ECHUU_SRC / "__init__.py",
    ECHUU_SRC / "core" / "__init__.py",
}

_USAGE_PATTERNS = {
    "TriggerBank(": re.compile(r"\bTriggerBank\s*\("),
    "DramaAmplifier(": re.compile(r"\bDramaAmplifier\s*\("),
    "StoryNucleus(": re.compile(r"\bStoryNucleus\s*\("),
    "DigressionDB(": re.compile(r"\bDigressionDB\s*\("),
    ".generate_digression(": re.compile(r"\.generate_digression\s*\("),
    ".inject_digression(": re.compile(r"\.inject_digression\s*\("),
    ".find_injection_point(": re.compile(r"\.find_injection_point\s*\("),
}


def _iter_source_files():
    for path in sorted(ECHUU_SRC.rglob("*.py")):
        yield path


def test_no_leak_literals_anywhere_in_shipped_source():
    """海苔/腰果/汇率/七块/四块八 must not exist anywhere under echuu/, used or not."""
    offenders = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for literal in _BANNED_LITERALS:
            if literal in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {literal!r}")
    assert not offenders, "example leak literals found:\n" + "\n".join(offenders)


def test_leak_prone_symbols_are_not_used_outside_their_own_modules():
    """Nothing in the shipped source calls/instantiates these dead modules.

    If this starts failing, someone wired TriggerBank/DramaAmplifier/
    StoryNucleus/DigressionDB back into the pipeline — that's fine, but it
    must come with a deliberate review of what content they now emit
    (content lock above) and an update to this allowlist, not a silent
    reconnection.
    """
    offenders = []
    for path in _iter_source_files():
        if path in _DEFINING_FILES or path in _REEXPORT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in _USAGE_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {label}")
    # Test doubles are fine — they simulate the interface without pulling in
    # real example content. Only echuu/ source (checked above) has to be clean.
    assert not offenders, (
        "leak-prone example modules are now referenced outside their own "
        "files; review their runtime output for canned example content "
        "before reconnecting them:\n" + "\n".join(offenders)
    )


def test_live_generation_entrypoints_do_not_import_leak_prone_modules():
    """Direct import-line check on the actual live-broadcast source files."""
    live_dirs = [ECHUU_SRC / "live", ECHUU_SRC / "generators"]
    banned_imports = ("trigger_bank", "drama_amplifier", "story_nucleus")
    offenders = []
    for live_dir in live_dirs:
        for path in sorted(live_dir.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in banned_imports:
                if re.search(rf"\bimport\s+.*\b{name}\b|\bfrom\s+\S*{name}\b", text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_trigger_bank_runtime_output_has_no_leak_literals():
    from echuu.core.trigger_bank import TriggerBank

    bank = TriggerBank()
    config = {
        "sensory_anchors": {"food": ["测试食物"], "things": ["测试物品"], "smells": ["测试气味"]},
        "pet_name": "测试宠物",
        "unconscious_habits": ["测试习惯"],
        "danmaku_content": True,
    }
    for _ in range(50):
        result = bank.sample(config, language="zh")
        for literal in _BANNED_LITERALS:
            assert literal not in result["filled"]


def test_drama_amplifier_examples_have_no_leak_literals():
    from echuu.core.drama_amplifier import DramaAmplifier

    for entry in DramaAmplifier().list_amplifiers().values():
        for literal in _BANNED_LITERALS:
            assert literal not in entry["before"]
            assert literal not in entry["after"]


def test_story_nucleus_output_has_no_leak_literals():
    from echuu.core.story_nucleus import StoryNucleus

    nucleus = StoryNucleus()
    for topic in ("测试主题一", "测试主题二", "离谱的事", "选择代价", "其实但是矛盾"):
        for _ in range(10):
            result = nucleus.generate_nucleus(topic)
            for literal in _BANNED_LITERALS:
                assert literal not in result["abnormality"]["description"]


def test_digression_db_chains_have_no_leak_literals():
    from echuu.core.digression_db import DigressionDB

    db = DigressionDB()
    for chain_type in DigressionDB.CHAINS:
        for _ in range(20):
            text = db.generate_digression(chain_type, "测试话题")
            for literal in _BANNED_LITERALS:
                assert literal not in text
