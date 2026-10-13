from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest
import yaml


@pytest.mark.parametrize("system,machine,version", [
    ("linux", "x86_64", "3.11"),
    ("linux", "x86_64", "3.13"),
    ("darwin", "x86_64", "3.11"),
    ("darwin", "x86_64", "3.13"),
    ("darwin", "arm64", "3.13"),
])
def test_lock_has_one_compatible_pin_per_declared_dependency(system, machine, version):
    environment = {**default_environment(), "sys_platform": system,
                   "platform_machine": machine, "python_version": version,
                   "python_full_version": version + ".0"}
    selected = {}
    for line in Path("requirements.lock").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        assert name not in selected, f"Duplicate active pin: {requirement.name}"
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "=="
        selected[name] = specifiers[0].version
    for line in Path("requirements.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        assert name in selected, f"Unlocked dependency: {requirement.name}"
        assert selected[name] in requirement.specifier


def test_ci_covers_report_python_and_both_workflows_install_the_lock():
    ci = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    reporting = yaml.safe_load(Path(".github/workflows/telegram-report.yml").read_text())
    report_steps = reporting["jobs"]["report"]["steps"]
    report_python = next(step["with"]["python-version"] for step in report_steps
                         if step.get("uses", "").startswith("actions/setup-python@"))
    assert report_python in ci["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    for steps in (ci["jobs"]["test"]["steps"], report_steps):
        installs = [step["run"] for step in steps if "pip install" in step.get("run", "")]
        assert installs and all("-r requirements.lock" in command for command in installs)
