#!/usr/bin/env python3
"""Verify that Buildkite provisioning runs from an exact reviewed main commit."""
from __future__ import annotations

import argparse
import pathlib
import re
import stat
import subprocess
from typing import NoReturn

EXPECTED_REPOSITORY = "git@github.com:1-vps/hostpanel.git"
HELPER_PATH = ".buildkite/operator/verify-provisioning-source.py"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> NoReturn:
    raise SystemExit(f"HostPanel Buildkite provisioning source rejected: {message}")


def git_environment() -> dict[str, str]:
    # Do not inherit Git configuration or repository-selection knobs from the
    # operator shell. Replacement objects are also disabled so an untrusted
    # provisioning clone cannot substitute another tree for the externally
    # reviewed commit while preserving its public SHA in rev-parse output.
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def git(repository: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            f"safe.directory={repository}",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repository),
            *args,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=git_environment(),
    )
    if result.returncode != 0:
        fail(
            f"git {' '.join(args)}: "
            f"{result.stderr.strip() or result.stdout.strip() or 'git command failed'}"
        )
    return result.stdout.rstrip("\n")


def normalize_path(value: str) -> str:
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        fail("--source-path values must be normalized repository-relative paths")
    return path.as_posix()


def verify_blob(repository: pathlib.Path, commit: str, relative_path: str) -> None:
    path = repository / relative_path
    try:
        info = path.lstat()
    except OSError as exc:
        fail(f"cannot inspect {relative_path}: {exc}")
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        fail(f"{relative_path} is not a regular non-symlink file")
    actual = git(repository, "hash-object", "--no-filters", "--", str(path))
    expected = git(repository, "rev-parse", f"{commit}:{relative_path}")
    if actual != expected:
        fail(f"{relative_path} does not match the reviewed commit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--source-path", action="append", required=True)
    args = parser.parse_args()

    expected_commit = args.expected_commit.lower()
    if not SHA_PATTERN.fullmatch(expected_commit) or expected_commit != args.expected_commit:
        fail("--expected-commit must be a full lowercase 40-hex SHA")

    repository = pathlib.Path(args.repository).resolve(strict=True)
    if not repository.is_dir():
        fail("--repository is not a directory")

    top_level = pathlib.Path(git(repository, "rev-parse", "--show-toplevel")).resolve()
    if top_level != repository:
        fail("--repository is not the exact Git top level")
    if git(repository, "remote", "get-url", "origin") != EXPECTED_REPOSITORY:
        fail("origin is not the reviewed HostPanel SSH repository")

    commit = git(repository, "rev-parse", "--verify", "HEAD^{commit}")
    if not SHA_PATTERN.fullmatch(commit):
        fail("HEAD is not a canonical commit SHA")
    if commit != expected_commit:
        fail("HEAD does not equal the externally reviewed commit")
    remote_main = git(repository, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}")
    if remote_main != commit:
        fail("HEAD does not equal refs/remotes/origin/main")

    source_paths = {HELPER_PATH, *(normalize_path(value) for value in args.source_path)}
    for relative_path in sorted(source_paths):
        verify_blob(repository, commit, relative_path)

    status = git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        fail("source worktree is not clean")
    print(commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
