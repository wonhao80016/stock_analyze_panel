"""scripts/upgrade_check.py: merge-tree conflict detection and output encoding."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "upgrade_check.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("upgrade_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upgrade_check = _load_script()

# Captured from `git merge-tree <base> <ours> <theirs>` with git 2.52.
MERGE_TREE_OUTPUT = """\
added in remote
  their  100644 5f37cb4edf4bad3880123f8fbd499da9a5429b3c added.txt
@@ -0,0 +1,3 @@
+title
+=======
+body
changed in both
  base   100644 de980441c3ab03a8c07dda1ad27b8a11f39deb1e both.txt
  our    100644 6c184ee0461b82ebe25680c3d3315bf123e59aab both.txt
  their  100644 af703352c64a2d88d4f62818fa68e6ae91241dfd both.txt
@@ -1,3 +1,7 @@
 a
+<<<<<<< .our
 FEATURE
+=======
+MAIN
+>>>>>>> .their
 c
added in both
  our    100644 d808023d793b019089c0d5b844b89560e08d81fb dup.txt
  their  100644 bd724b91d544640b2a32ff4475865a794098ff87 dup.txt
@@ -1,2 +1,6 @@
 same
+<<<<<<< .our
 feat
+=======
+main
+>>>>>>> .their
changed in both
  base   100644 535d2b01d3397c2228490875defc92370602ca46 sep.txt
  our    100644 409eec78f6162a8d5018fc0f2bd82bfae31dd698 sep.txt
  their  100644 846d1d199ae22a6b13a9a3769317ca73648ff41c sep.txt
@@ -6,4 +6,5 @@
 5
 6
 7
-8
+main8
+=======
"""


def test_legacy_parser_ignores_separator_lines_outside_conflicts():
    assert upgrade_check._parse_legacy_merge_tree(MERGE_TREE_OUTPUT) == ["both.txt", "dup.txt"]


def test_legacy_parser_treats_one_sided_addition_with_separator_as_clean():
    output = "added in remote\n  their  100644 5f37cb4e notes.md\n@@ -0,0 +1,3 @@\n+a\n+=======\n+b\n"

    assert upgrade_check._parse_legacy_merge_tree(output) == []


def test_legacy_parser_reports_conflicted_path_with_spaces_whole():
    output = (
        "changed in both\n"
        "  base   100644 aaa docs/my notes.md\n"
        "  our    100644 bbb docs/my notes.md\n"
        "  their  100644 ccc docs/my notes.md\n"
        "@@ -1 +1,5 @@\n"
        "+<<<<<<< .our\n"
        " ours\n"
        "+=======\n"
        "+theirs\n"
        "+>>>>>>> .their\n"
    )

    assert upgrade_check._parse_legacy_merge_tree(output) == ["docs/my notes.md"]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
         "-c", "core.autocrlf=false", *args],
        cwd=repo, capture_output=True, encoding="utf-8", check=True,
    )
    return result.stdout


CHINESE_MD = "中文說明.md"
EXPECTED_CONFLICTS = sorted(["both.txt", "moddel.txt", "image.bin", "new.bin", CHINESE_MD])


@pytest.fixture
def diverged_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "both.txt").write_text("a\nb\nc\n", encoding="utf-8")
    (repo / "sep.txt").write_text("1\n2\n3\n4\n5\n6\n7\n8\n", encoding="utf-8")
    (repo / "moddel.txt").write_text("keep\n", encoding="utf-8")
    (repo / "image.bin").write_bytes(b"\x00\x01base\x00")
    (repo / CHINESE_MD).write_text("# 標題\n原始內容\n結尾\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")

    _git(repo, "checkout", "-qb", "upstream")
    (repo / "both.txt").write_text("a\nUPSTREAM\nc\n", encoding="utf-8")
    (repo / "sep.txt").write_text("1\n2\n3\n4\n5\n6\n7\nupstream8\n=======\n", encoding="utf-8")
    (repo / "added.md").write_text("title\n=======\nbody\n", encoding="utf-8")
    (repo / "moddel.txt").write_text("changed upstream\n", encoding="utf-8")
    (repo / "image.bin").write_bytes(b"\x00\x01upstream\x00")
    (repo / "new.bin").write_bytes(b"\x00\x02upstream\x00")
    (repo / CHINESE_MD).write_text("# 標題\n上游修改的內容\n結尾\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "upstream")

    _git(repo, "checkout", "-q", "main")
    (repo / "both.txt").write_text("a\nLOCAL\nc\n", encoding="utf-8")
    (repo / "sep.txt").write_text("1\n2\nlocal3\n4\n5\n6\n7\n8\n", encoding="utf-8")
    _git(repo, "rm", "-q", "moddel.txt")
    (repo / "image.bin").write_bytes(b"\x00\x01local\x00")
    (repo / "new.bin").write_bytes(b"\x00\x02local\x00")
    (repo / CHINESE_MD).write_text("# 標題\n本地修改的內容\n結尾\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "local")
    return repo


def _commits(repo: Path) -> tuple[str, str, str]:
    current = _git(repo, "rev-parse", "HEAD").strip()
    target = _git(repo, "rev-parse", "upstream").strip()
    return _git(repo, "merge-base", current, target).strip(), current, target


def test_merge_tree_reports_text_modify_delete_and_binary_conflicts(diverged_repo):
    base, current, target = _commits(diverged_repo)

    assert upgrade_check._merge_tree_conflicts(diverged_repo, base, current, target) == EXPECTED_CONFLICTS


def test_old_git_falls_back_to_legacy_merge_tree_with_a_warning(diverged_repo, monkeypatch, capsys):
    base, current, target = _commits(diverged_repo)
    monkeypatch.setattr(upgrade_check, "_git_version", lambda root: (2, 37))

    conflicts = upgrade_check._merge_tree_conflicts(diverged_repo, base, current, target)

    assert conflicts == sorted(["both.txt", CHINESE_MD])
    assert "2.38" in capsys.readouterr().err


def test_piped_output_on_a_cp950_locale_does_not_crash(diverged_repo):
    env = {**os.environ, "PYTHONIOENCODING": "cp950", "PYTHONUTF8": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "upstream", "--repo", str(diverged_repo)],
        capture_output=True, env=env, timeout=60,
    )

    stderr = result.stderr.decode("utf-8", errors="replace")
    assert "UnicodeEncodeError" not in stderr
    assert "UnicodeDecodeError" not in stderr
    assert result.returncode == 2, stderr
    stdout = result.stdout.decode("utf-8")
    assert "仓库" in stdout
    for path in EXPECTED_CONFLICTS:
        assert f"  - {path}" in stdout
