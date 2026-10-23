#!/usr/bin/env python3
"""Read-only Git overlap and conflict preview for secondary-development branches."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result


def _lines(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _resolve_repo(path: Path) -> Path:
    result = _git(path, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def _resolve_commit(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


WRITE_TREE_MIN_GIT = (2, 38)


def _git_version(root: Path) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", _git(root, "--version").stdout)
    if not match:
        raise RuntimeError("cannot parse git --version output")
    return int(match.group(1)), int(match.group(2))


def _merge_tree_conflicts(root: Path, base: str, current: str, target: str) -> list[str]:
    if _git_version(root) < WRITE_TREE_MIN_GIT:
        print(
            "警告: Git 版本低于 2.38, 改用旧版 merge-tree 预演, 只能识别文本冲突, "
            "会漏判修改/删除与二进制文件冲突。",
            file=sys.stderr,
        )
        return _legacy_merge_tree_conflicts(root, base, current, target)
    # Exit status 0 means clean and 1 means conflicts; with -z the output is the tree OID
    # followed by NUL-terminated conflicted paths. Writes only unreferenced objects, no refs.
    result = _git(
        root, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", current, target,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "git merge-tree --write-tree failed")
    return sorted({path for path in result.stdout.split("\0")[1:] if path})


def _legacy_merge_tree_conflicts(root: Path, base: str, current: str, target: str) -> list[str]:
    # Fallback for Git < 2.38. Reports only "in both" sections whose merged diff adds a
    # "+<<<<<<< .our" marker, so modify/delete and binary conflicts are not detected.
    result = _git(root, "merge-tree", base, current, target)
    return _parse_legacy_merge_tree(result.stdout)


def _parse_legacy_merge_tree(output: str) -> list[str]:
    conflicts: set[str] = set()
    section = ""
    path: str | None = None
    for line in output.splitlines():
        if line and line[0] not in " +-@\\":
            section, path = line, None
            continue
        if not section.endswith(" in both"):
            continue
        entry = line.split(maxsplit=3)
        if path is None and len(entry) == 4 and entry[0] in {"base", "our", "their"}:
            path = entry[3]
        elif line == "+<<<<<<< .our" and path:
            conflicts.add(path)
    return sorted(conflicts)


def _print_paths(title: str, paths: list[str]) -> None:
    print(f"\n{title}: {len(paths)}")
    for path in paths:
        print(f"  - {path}")


def _use_utf8_output() -> None:
    # Piped or redirected output otherwise uses the locale code page (cp950 on zh-TW Windows),
    # which cannot encode the Simplified Chinese messages.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    parser = argparse.ArgumentParser(
        description="只读检查当前二开分支升级到目标 Git ref 时的源码重叠与文本冲突",
    )
    parser.add_argument("target", help="目标 Tag/分支/commit, 例如 v0.3.1")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="仓库路径, 默认当前目录",
    )
    args = parser.parse_args(argv)

    try:
        root = _resolve_repo(args.repo.resolve())
        current = _resolve_commit(root, "HEAD")
        target = _resolve_commit(root, args.target)
        base = _git(root, "merge-base", current, target).stdout.strip()
        if not base:
            raise RuntimeError("当前分支与目标版本没有共同基线")

        current_paths = _lines(_git(root, "diff", "--name-only", f"{base}..{current}"))
        target_paths = _lines(_git(root, "diff", "--name-only", f"{base}..{target}"))
        overlap = sorted(current_paths & target_paths)
        conflicts = _merge_tree_conflicts(root, base, current, target)
        dirty = sorted(_lines(_git(root, "status", "--short")))

        print("Itoy 的台股監控看板 二开升级预检")
        print(f"仓库: {root}")
        print(f"当前: {current[:12]}")
        print(f"目标: {args.target} ({target[:12]})")
        print(f"共同基线: {base[:12]}")
        print(f"当前分支修改文件: {len(current_paths)}")
        print(f"目标版本修改文件: {len(target_paths)}")
        _print_paths("双方修改的同一文件", overlap)
        _print_paths("Git 三方预演识别的文本冲突", conflicts)

        if dirty:
            print(f"\n未提交工作区条目: {len(dirty)}")
            print("  注意: 未提交内容不会进入 Git 三方预演, 请先提交到临时分支后再复检。")
        if conflicts:
            print("\n结论: 需要人工解决文本冲突并执行完整回归。")
            return 2
        if overlap:
            print("\n结论: Git 未发现文本冲突, 但重叠文件需要检查语义兼容和测试。")
            return 0
        print("\n结论: 未发现源码重叠; 仍需执行目标版本的验证矩阵。")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"升级预检失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
