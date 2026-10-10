#!/usr/bin/env python3
"""Compose the ARIS Codex installer with a copied Brain++ skill and GPU rules."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- ARIS-BRAINPP:BEGIN -->"
END = "<!-- ARIS-BRAINPP:END -->"
SKILL_REL = Path(".agents/skills/brainpp-rjob")
MANIFEST_REL = Path(".aris/installed-brainpp.json")
VALUE_FLAGS = {"--aris-repo", "--groups", "--skills", "--exclude", "--replace-link"}
SWITCH_FLAGS = {
    "--reconcile", "--uninstall", "--with-claude-review-overlay",
    "--with-gemini-review-overlay", "--dry-run", "--quiet", "--clear-stale-lock",
    "--all", "--add-new", "--skip-new", "--list-groups",
}
HELP = """Usage: bash tools/install_aris_brainpp.sh [project_path] [options]

Install ARIS Codex skills, copy brainpp-rjob, and add Brain++ rules to AGENTS.md.
Default project: current directory. Repeat the command to update the package.
ARIS skills retain the original installer's symlink layout; brainpp-rjob is copied.

All selection/overlay/action options from install_aris_codex.sh are forwarded.
  --dry-run             preview both installations without project writes
  --reconcile           update an existing ARIS installation and Brain++ package
  --uninstall           remove ARIS and unchanged, owned Brain++ package files
  --brainpp-package DIR  use a different package (skills/brainpp-rjob + agents-fragment.md)
  --list-groups         list ARIS groups only; do not install Brain++

Local edits to managed Brain++ files or its AGENTS.md block cause a conflict.
Put project overrides outside the ARIS-CODEX and ARIS-BRAINPP managed blocks.
--no-doc is unsupported because the Brain++ rules are part of this package.
This installer does not run rjob, install conda environments, or submit GPU jobs.
"""


def fail(message: str) -> None:
    raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_args(args: list[str]) -> tuple[Path, Path, list[str]]:
    project = None
    package = REPO_ROOT / "packages/brainpp"
    forwarded: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--no-doc":
            fail("--no-doc is incompatible with installing Brain++ GPU rules")
        if item in VALUE_FLAGS or item == "--brainpp-package":
            if index + 1 == len(args):
                fail(f"{item} requires a value")
            value = args[index + 1]
            if item == "--brainpp-package":
                package = Path(value).expanduser()
            else:
                forwarded.extend([item, value])
            index += 2
            continue
        if item in SWITCH_FLAGS:
            forwarded.append(item)
        elif item.startswith("--"):
            fail(f"unknown option: {item}")
        elif project is None:
            project = Path(item).expanduser()
        else:
            fail(f"unexpected project argument: {item}")
        index += 1
    if "--reconcile" in forwarded and "--uninstall" in forwarded:
        fail("--reconcile and --uninstall cannot be combined")
    return (project or Path.cwd()).resolve(), package.resolve(), forwarded


def check_paths(project: Path) -> None:
    if not project.is_dir():
        fail(f"project directory does not exist: {project}")
    for rel in (".aris", ".agents", ".agents/skills", str(SKILL_REL),
                "AGENTS.md", str(MANIFEST_REL)):
        if (project / rel).is_symlink():
            fail(f"refusing to modify a symlink: {project / rel}")
    doc = project / "AGENTS.md"
    if doc.exists() and not doc.is_file():
        fail(f"AGENTS.md is not a regular file: {doc}")


def package_files(package: Path) -> tuple[dict[str, bytes], str]:
    source = package / "skills/brainpp-rjob"
    required = ("SKILL.md", "agents/openai.yaml", "references/rjob-operations.md",
                "references/experiment-loop.md", "scripts/submit_gpu_smoke.py",
                "scripts/gpu_smoke.sh")
    files = {}
    for rel in required:
        path = source / rel
        if path.is_symlink() or not path.is_file():
            fail(f"missing regular package file: {path}")
        files[rel] = path.read_bytes()
    fragment = (package / "agents-fragment.md").read_text(encoding="utf-8").strip()
    if not fragment or BEGIN in fragment or END in fragment:
        fail("agents-fragment.md must contain non-empty rules without managed markers")
    return files, f"{BEGIN}\n{fragment}\n{END}"


def read_manifest(project: Path) -> dict | None:
    path = project / MANIFEST_REL
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("skill_path") != str(SKILL_REL):
        fail(f"unsupported Brain++ manifest: {path}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        fail(f"invalid Brain++ file manifest: {path}")
    for rel, sha in files.items():
        part = Path(rel)
        if (part.is_absolute() or ".." in part.parts or str(part) != rel
                or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
            fail(f"invalid Brain++ manifest entry: {rel}")
    if not isinstance(manifest.get("owned_skill"), bool):
        fail("Brain++ manifest has no valid skill ownership")
    if not isinstance(manifest.get("owned_block"), bool):
        fail("Brain++ manifest has no valid block ownership")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("block_sha256", ""))):
        fail("Brain++ manifest has no valid AGENTS.md block hash")
    return manifest


def read_doc(project: Path) -> tuple[str, str | None]:
    path = project / "AGENTS.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        fail("AGENTS.md has duplicate or incomplete ARIS-BRAINPP markers")
    start, end = text.find(BEGIN), text.find(END)
    if end >= 0 and end < start:
        fail("AGENTS.md has reversed ARIS-BRAINPP markers")
    return text, text[start:end + len(END)] if start >= 0 else None


def check_skill_path(project: Path, rel: str) -> Path:
    path = project / SKILL_REL / rel
    # Reject symlinked intermediate directories as well as the file itself.
    parts = [path]
    parent = path.parent
    while parent != project:
        parts.append(parent)
        parent = parent.parent
    if any(part.is_symlink() for part in parts):
        fail(f"Brain++ skill contains a symlink: {path}")
    return path


def check_file(project: Path, rel: str, sha: str) -> None:
    path = check_skill_path(project, rel)
    if not path.is_file() or digest(path.read_bytes()) != sha:
        fail(f"local Brain++ change or missing file: {path}; preserve/review it before updating")


def preflight(project: Path, files: dict[str, bytes], block: str,
              uninstall: bool) -> tuple[dict | None, bool, bool]:
    check_paths(project)
    manifest = read_manifest(project)
    _, existing_block = read_doc(project)
    target = project / SKILL_REL
    for rel in files:
        check_skill_path(project, rel)
    if manifest:
        for rel, sha in manifest["files"].items():
            check_file(project, rel, sha)
        if existing_block is None or digest(existing_block.encode()) != manifest["block_sha256"]:
            fail("local Brain++ AGENTS.md block change; preserve/review it before updating")
        for rel in files.keys() - manifest["files"].keys():
            if (target / rel).exists() or (target / rel).is_symlink():
                fail(f"new package file conflicts with existing local file: {target / rel}")
        return manifest, manifest["owned_skill"], manifest["owned_block"]
    if uninstall:
        fail("no Brain++ manifest; refusing to uninstall unmanaged files")
    if target.exists():
        if not target.is_dir():
            fail(f"Brain++ skill path is not a directory: {target}")
        for rel, data in files.items():
            check_file(project, rel, digest(data))
    if existing_block is not None and existing_block != block:
        fail("unmanaged Brain++ AGENTS.md block differs; refusing to overwrite")
    return None, not target.exists(), existing_block is None


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        else:
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_aris(command: list[str]) -> tuple[int, bool]:
    """Keep plans visible before interactive approval, and detect cancellation."""
    cancelled = False
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            cancelled |= line.strip() == "aborted"
        return process.wait(), cancelled


def apply_package(project: Path, package: Path, files: dict[str, bytes], block: str,
                  uninstall: bool) -> None:
    # Recheck after ARIS has finished so intervening local edits are not overwritten.
    manifest, owned_skill, owned_block = preflight(project, files, block, uninstall)
    text, existing_block = read_doc(project)
    target = project / SKILL_REL
    if uninstall:
        if owned_skill:
            for rel in manifest["files"]:
                (target / rel).unlink()
            # Remove only empty directories; preserve added local files/results.
            for directory in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if directory.is_dir() and not directory.is_symlink():
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            try:
                target.rmdir()
            except OSError:
                pass
        if owned_block:
            atomic_write(project / "AGENTS.md", text.replace(existing_block, "", 1).encode())
        (project / MANIFEST_REL).unlink()
        print("Brain++ uninstalled; pre-existing skills, user notes and added files preserved.")
        return
    for rel, data in files.items():
        path = target / rel
        if not path.exists() or path.read_bytes() != data:
            atomic_write(path, data)
    if manifest:
        for rel in manifest["files"].keys() - files.keys():
            (target / rel).unlink()
    if existing_block is None:
        updated = text + ("\n\n" if text and not text.endswith("\n\n") else "") + block + "\n"
    else:
        updated = text.replace(existing_block, block, 1)
    if updated != text:
        atomic_write(project / "AGENTS.md", updated.encode())
    record = {
        "version": 1, "package_source": str(package), "skill_path": str(SKILL_REL),
        "files": {rel: digest(data) for rel, data in files.items()},
        "block_sha256": digest(block.encode()), "owned_skill": owned_skill,
        "owned_block": owned_block,
    }
    atomic_write(project / MANIFEST_REL, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode())
    print(f"Brain++ installed: {target} (copied files; no GPU jobs submitted).")


def main(args: list[str]) -> int:
    if "--help" in args or "-h" in args:
        print(HELP)
        return 0
    project, package, forwarded = parse_args(args)
    installer = REPO_ROOT / "tools/install_aris_codex.sh"
    command = ["bash", str(installer), str(project), *forwarded]
    if "--list-groups" in forwarded:
        return subprocess.run(command).returncode
    uninstall = "--uninstall" in forwarded
    files, block = package_files(package)
    preflight(project, files, block, uninstall)
    print(f"Brain++ package: {package}", flush=True)
    print(f"Brain++ target: {project / SKILL_REL} (copied skill)", flush=True)
    print("GPU rules: AGENTS.md / ARIS-BRAINPP; separate ownership manifest", flush=True)
    if "--dry-run" in forwarded:
        if not uninstall or (project / ".aris/installed-skills-codex.txt").exists():
            result = subprocess.run(command)
            if result.returncode:
                return result.returncode
        print(f"(dry-run) would {'remove' if uninstall else 'install/update'} Brain++ at {project / SKILL_REL}")
        print("(dry-run) AGENTS.md uses a separate ARIS-BRAINPP block; no project writes")
        return 0

    state_dir = project / ".aris"
    state_dir.mkdir(exist_ok=True)
    lock = state_dir / ".install-brainpp.lock.d"
    try:
        lock.mkdir()
    except FileExistsError:
        fail(f"Brain++ installation lock exists: {lock}; check its owner before removing it")
    try:
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid()}))
        preflight(project, files, block, uninstall)
        aris_manifest = project / ".aris/installed-skills-codex.txt"
        if not uninstall or aris_manifest.exists():
            returncode, cancelled = run_aris(command)
            if returncode:
                return returncode
            if cancelled:
                print("Brain++ unchanged because ARIS installation was cancelled.")
                return 0
        if not uninstall and not aris_manifest.is_file():
            fail("ARIS installation did not produce its manifest; Brain++ unchanged")
        apply_package(project, package, files, block, uninstall)
    finally:
        (lock / "owner.json").unlink(missing_ok=True)
        lock.rmdir()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
