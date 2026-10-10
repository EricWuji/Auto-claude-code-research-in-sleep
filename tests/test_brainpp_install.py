"""Exercise real install/reconcile/uninstall operations in isolated projects."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "tools/install_aris_brainpp.sh"
BASE = ROOT / "tools/install_aris_codex.sh"
PACKAGE = ROOT / "packages/brainpp"
SKILL = Path(".agents/skills/brainpp-rjob")


@pytest.fixture
def installation(tmp_path: Path):
    repo = tmp_path / "aris"
    for name in ("alpha", "beta"):
        directory = repo / "skills/skills-codex" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"# {name}\n")
    overlay = repo / "skills/skills-codex-claude-review/beta"
    overlay.mkdir(parents=True)
    (overlay / "SKILL.md").write_text("# beta Claude overlay\n")
    (repo / "tools").mkdir()
    (repo / "tools/skill-groups.tsv").write_text(
        "group\texperiments\tExperiments\tCompute skills\n"
        "group\tother\tOther\tOther skills\n"
        "skill\talpha\texperiments\t-\talpha\n"
        "skill\tbeta\tother\t-\tbeta\n"
    )
    project = tmp_path / "project with spaces"
    project.mkdir()
    home = tmp_path / "isolated-home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), ARIS_NO_PICKER="1")
    package = tmp_path / "brainpp package"
    shutil.copytree(PACKAGE, package)

    def invoke(*options: str, check=True, input=None, base=False):
        command = ["bash", str(BASE if base else WRAPPER), str(project),
                   "--aris-repo", str(repo)]
        if not base:
            command.extend(["--brainpp-package", str(package)])
        command.extend(options)
        return subprocess.run(command, env=env, cwd=tmp_path, text=True,
                              capture_output=True, check=check, input=input)

    return project, repo, package, home, invoke


def test_dry_run_writes_neither_project_nor_global_pointer(installation):
    project, _, _, home, invoke = installation
    result = invoke("--all", "--dry-run")
    assert "would install/update Brain++" in result.stdout
    assert list(project.iterdir()) == []
    assert list(home.iterdir()) == []


def test_install_repeat_update_and_uninstall_preserve_user_content(installation):
    project, repo, package, _, invoke = installation
    doc = project / "AGENTS.md"
    user_note = "# My research project\n\n用户明确覆盖的资源与预算。\n"
    doc.write_text(user_note)
    invoke("--all", "--quiet")
    skill = project / SKILL
    assert skill.is_dir() and not skill.is_symlink()
    assert (project / ".agents/skills/alpha").resolve() == repo / "skills/skills-codex/alpha"
    for source in (package / "skills/brainpp-rjob").rglob("*"):
        if source.is_file():
            assert (skill / source.relative_to(package / "skills/brainpp-rjob")).read_bytes() == source.read_bytes()
    first_doc = doc.read_text()
    first_manifest = (project / ".aris/installed-brainpp.json").read_bytes()
    invoke("--quiet")
    # ARIS normalizes its own block/newline on reconcile; the Brain++ block
    # and ownership record must stay unchanged, without duplicate rules.
    assert doc.read_text().split("<!-- ARIS-BRAINPP:BEGIN -->", 1)[1].rstrip() == (
        first_doc.split("<!-- ARIS-BRAINPP:BEGIN -->", 1)[1].rstrip()
    )
    assert (project / ".aris/installed-brainpp.json").read_bytes() == first_manifest
    assert doc.read_text().count("<!-- ARIS-BRAINPP:BEGIN -->") == 1
    assert first_doc.startswith(user_note)

    source = package / "skills/brainpp-rjob/SKILL.md"
    source.write_text(source.read_text() + "\nPackage update.\n")
    (package / "agents-fragment.md").write_text(
        (package / "agents-fragment.md").read_text() + "\nUpdated execution guidance.\n"
    )
    invoke("--reconcile", "--quiet")
    assert (skill / "SKILL.md").read_bytes() == source.read_bytes()
    assert "Updated execution guidance." in doc.read_text()
    (skill / "my-results.json").write_text('{"metric": 1}')
    invoke("--uninstall", "--quiet")
    assert not (skill / "SKILL.md").exists()
    assert (skill / "my-results.json").read_text() == '{"metric": 1}'
    assert user_note in doc.read_text()
    assert "ARIS-BRAINPP:BEGIN" not in doc.read_text()
    assert "ARIS-CODEX:BEGIN" not in doc.read_text()
    assert not (project / ".aris/installed-brainpp.json").exists()


def test_selection_and_reviewer_overlay_are_forwarded(installation):
    project, repo, _, _, invoke = installation
    invoke("--groups", "experiments", "--skills", "beta",
           "--with-claude-review-overlay", "--quiet")
    assert (project / ".agents/skills/beta").resolve() == repo / "skills/skills-codex-claude-review/beta"
    assert (project / SKILL / "SKILL.md").exists()


@pytest.mark.parametrize("change", ["skill", "block", "duplicate", "symlink"])
def test_local_changes_block_reconcile_and_uninstall_before_aris_writes(installation, change):
    project, _, _, _, invoke = installation
    invoke("--all", "--quiet")
    doc = project / "AGENTS.md"
    file = project / SKILL / "SKILL.md"
    if change == "skill":
        file.write_text(file.read_text() + "\nMy custom settings\n")
    elif change == "block":
        doc.write_text(doc.read_text().replace("gpu: brainpp", "gpu: custom"))
    elif change == "duplicate":
        doc.write_text(doc.read_text() + "<!-- ARIS-BRAINPP:BEGIN -->\nx\n<!-- ARIS-BRAINPP:END -->\n")
    else:
        script = project / SKILL / "scripts/gpu_smoke.sh"
        script.unlink()
        script.symlink_to(file)
    before_doc = doc.read_bytes()
    manifest = project / ".aris/installed-skills-codex.txt"
    before_manifest = manifest.read_bytes()
    for action in ("--reconcile", "--uninstall"):
        result = invoke(action, "--quiet", check=False)
        assert result.returncode != 0
        assert "error:" in result.stderr
        assert doc.read_bytes() == before_doc
        assert manifest.read_bytes() == before_manifest
    assert not (project / ".aris/.install-brainpp.lock.d").exists()


def test_matching_preexisting_skill_and_local_results_survive_uninstall(installation):
    project, _, package, _, invoke = installation
    skill = project / SKILL
    shutil.copytree(package / "skills/brainpp-rjob", skill)
    (skill / "scripts/old-smoke.log").write_text("historical result")
    invoke("--all", "--quiet")
    record = json.loads((project / ".aris/installed-brainpp.json").read_text())
    assert record["owned_skill"] is False
    invoke("--uninstall", "--quiet")
    assert (skill / "SKILL.md").is_file()
    assert (skill / "scripts/old-smoke.log").read_text() == "historical result"


def test_custom_unmanaged_skill_is_not_overwritten(installation):
    project, _, _, _, invoke = installation
    skill = project / SKILL
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("My custom rjob skill")
    result = invoke("--all", "--quiet", check=False)
    assert result.returncode != 0
    assert (skill / "SKILL.md").read_text() == "My custom rjob skill"
    assert not (project / ".aris").exists()


def test_plain_aris_reconcile_uninstall_preserve_brainpp(installation):
    project, _, _, _, invoke = installation
    invoke("--all", "--quiet")
    invoke("--reconcile", "--quiet", base=True)
    assert "ARIS-BRAINPP:BEGIN" in (project / "AGENTS.md").read_text()
    invoke("--uninstall", "--quiet", base=True)
    assert (project / SKILL / "SKILL.md").is_file()
    assert "ARIS-BRAINPP:BEGIN" in (project / "AGENTS.md").read_text()
    invoke("--uninstall", "--dry-run")
    assert (project / SKILL / "SKILL.md").is_file()
    invoke("--uninstall", "--quiet")
    assert not (project / SKILL).exists()


def test_declining_aris_install_does_not_install_brainpp(installation):
    project, _, _, _, invoke = installation
    result = invoke("--all", input="n\n")
    assert "cancelled" in result.stdout
    assert not (project / SKILL).exists()
    assert not (project / "AGENTS.md").exists()


def test_no_doc_is_rejected_and_list_groups_is_read_only(installation):
    project, _, _, home, invoke = installation
    result = invoke("--no-doc", check=False)
    assert result.returncode != 0
    assert "incompatible" in result.stderr
    result = invoke("--list-groups")
    assert "experiments" in result.stdout
    assert list(project.iterdir()) == []
    assert list(home.iterdir()) == []


def test_aris_failure_does_not_install_brainpp(installation):
    project, _, _, _, invoke = installation
    result = invoke("--skills", "unknown-skill", "--quiet", check=False)
    assert result.returncode != 0
    assert not (project / SKILL).exists()
    assert not (project / ".aris/installed-brainpp.json").exists()
    assert not (project / ".aris/.install-brainpp.lock.d").exists()


def test_real_aris_repository_and_package_install_together(tmp_path):
    project = tmp_path / "real-project"
    project.mkdir()
    home = tmp_path / "test-home"
    home.mkdir()
    result = subprocess.run(
        ["bash", str(WRAPPER), str(project), "--groups", "ideation,experiments,review-loop",
         "--with-claude-review-overlay", "--quiet"],
        env=dict(os.environ, HOME=str(home)), capture_output=True, text=True, check=True,
    )
    assert "Brain++ installed" in result.stdout
    assert (project / ".agents/skills/idea-discovery/SKILL.md").is_file()
    assert (project / ".agents/skills/experiment-bridge/SKILL.md").is_file()
    assert (project / ".agents/skills/auto-review-loop").resolve() == (
        ROOT / "skills/skills-codex-claude-review/auto-review-loop"
    )
    assert not (project / SKILL).is_symlink()
    packaged_files = {str(p.relative_to(project / SKILL))
                      for p in (project / SKILL).rglob("*") if p.is_file()}
    assert len(packaged_files) == 6
    assert not any(name.endswith(".log") or "gpu-smoke-job" in name for name in packaged_files)
    subprocess.run(["bash", str(WRAPPER), str(project), "--uninstall", "--quiet"],
                   env=dict(os.environ, HOME=str(home)), capture_output=True, text=True, check=True)
