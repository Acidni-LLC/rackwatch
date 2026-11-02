"""Secret scan, run by the pre-commit hook and CI. Exit 1 on any finding. Never prints a secret's value.

Three checks over tracked and staged files, plus untracked files that are not ignored:
1. the exact value of every API key in the local .env, if there is one (also over git history
   with --history, which is what to run before any push);
2. detect-secrets, skipping generated data, where hashes look like high-entropy strings;
3. no .env file is tracked or staged.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTROPY_SKIP = ("data/generated/", "data/replay/", "frontend/package-lock.json", "docs/preflight.json")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def repo_files() -> list[str]:
    names = set(git("ls-files", "--cached", "--others", "--exclude-standard").splitlines())
    return sorted(n for n in names if (ROOT / n).is_file())


KEY_NAMES = ("MODEL_API_KEY", "META_API_KEY", "BASETEN_API_KEY", "FIREWORKS_API_KEY")


def local_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    env_file = ROOT / ".env"
    sources = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    sources += [
        f"{k}={v}"
        for k, v in os.environ.items()
        if k in KEY_NAMES
    ]
    for line in sources:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip()
        if name.strip() in KEY_NAMES and len(value) >= 16:
            keys[name.strip()] = value
    return keys


def scan_exact(files: list[str], keys: dict[str, str]) -> list[str]:
    hits = []
    for name in files:
        data = (ROOT / name).read_bytes()
        for key_name, value in keys.items():
            if value.encode() in data:
                hits.append(f"{name}: contains the value of {key_name}")
    return hits


def scan_history(keys: dict[str, str]) -> list[str]:
    hits = []
    for key_name, value in keys.items():
        found = git("log", "--all", "--format=%h", f"-S{value}")
        if found.strip():
            hits.append(f"git history: {key_name} appears in commits {found.split()}")
    return hits


def scan_detect_secrets(files: list[str]) -> list[str]:
    targets = [
        f for f in files if not f.startswith(ENTROPY_SKIP) and not f.endswith((".jpg", ".png", ".wav"))
    ]
    if not targets:
        return []
    try:
        output = subprocess.run(
            [sys.executable, "-m", "detect_secrets", "scan", "--no-verify", *targets],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        return [f"detect-secrets did not run: {error}"]
    return [
        f"{name}:{r['line_number']}: {r['type']}"
        for name, rs in json.loads(output)["results"].items()
        for r in rs
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history", action="store_true", help="also scan all of git history for the exact keys"
    )
    args = parser.parse_args()
    files = repo_files()
    keys = local_keys()
    findings = []
    findings += [f"{n}: .env files must not be tracked" for n in files if Path(n).name == ".env"]
    findings += scan_exact(files, keys)
    findings += scan_detect_secrets(files)
    if args.history:
        findings += scan_history(keys)
    for finding in findings:
        print("SECRET SCAN:", finding)
    print(f"secret scan: {len(files)} files, {len(keys)} local keys checked, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
