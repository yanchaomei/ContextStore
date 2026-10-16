from __future__ import annotations

# Record exact static-validation commands and observable results.

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parent
SKILL = Path("/Users/yanchaomei/.codex/skills/competition-submission-studio")
SCRIPTS = SKILL / "scripts"
CREATOR = Path("/Users/yanchaomei/.codex/skills/.system/skill-creator/scripts")
commands = [
    [sys.executable, "-B", str(CREATOR / "quick_validate.py"), str(SKILL)],
    [
        "black",
        "--check",
        "--line-length",
        "100",
        str(SCRIPTS),
        str(BASE / "validate_helpers.py"),
        str(BASE / "record_checks.py"),
    ],
    [
        "ruff",
        "check",
        str(SCRIPTS),
        str(BASE / "validate_helpers.py"),
        str(BASE / "record_checks.py"),
    ],
    [
        sys.executable,
        "-B",
        "-c",
        "import pathlib, sys; "
        "[compile(pathlib.Path(p).read_bytes(), p, 'exec') for p in sys.argv[1:]]",
        *map(str, sorted(SCRIPTS.glob("*.py"))),
        str(BASE / "validate_helpers.py"),
        str(BASE / "record_checks.py"),
    ],
    [sys.executable, "-B", str(SCRIPTS / "package_audit.py"), "--help"],
    [sys.executable, "-B", str(SCRIPTS / "check_narration.py"), "--help"],
    ["ffprobe", "-version"],
]
results = []
for command in commands:
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    results.append(
        {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
entrypoint = (SKILL / "SKILL.md").read_text()
link_targets = re.findall(r"\]\((references/[^)]+)\)", entrypoint)
assert link_targets and all((SKILL / link).is_file() for link in link_targets)
assert len(entrypoint.splitlines()) <= 150
python_files = sorted(SCRIPTS.glob("*.py"))
assert all(
    path.read_text().splitlines()[0] == "from __future__ import annotations"
    for path in python_files
)
assert not any(
    "/Users/" in path.read_text() or "/home/" in path.read_text()
    for path in SKILL.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts
)
for path in (SKILL / "references").glob("*.json"):
    json.loads(path.read_text())
hashes = {
    str(path.relative_to(SKILL)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(SKILL.rglob("*"))
    if path.is_file() and "__pycache__" not in path.parts
}
helper_report = json.loads((BASE / "validation-report.json").read_text())
assert hashes == helper_report["skill_file_sha256"]
report = {
    "date_utc": datetime.now(timezone.utc).isoformat(),
    "all_command_exits_zero": all(result["exit_code"] == 0 for result in results),
    "commands": results,
    "entrypoint_lines": len(entrypoint.splitlines()),
    "linked_references_exist": link_targets,
    "implicit_invocation": True,
    "python_first_line_future_annotations": True,
    "no_hardcoded_private_paths_in_skill": True,
    "helper_validation_cases": helper_report["case_count"],
    "helper_validation_all_passed": helper_report["all_cases_passed"],
    "skill_file_sha256": hashes,
    "not_assessed": [
        "independent forward-test",
        "visual QA",
        "subjective audio quality",
        "provider synthesis",
        "publication",
        "competition submission",
    ],
}
(BASE / "static-checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
for cache in SKILL.rglob("__pycache__"):
    shutil.rmtree(cache)
print(
    json.dumps(
        {
            "all_command_exits_zero": report["all_command_exits_zero"],
            "helper_cases": report["helper_validation_cases"],
            "entrypoint_lines": report["entrypoint_lines"],
        }
    )
)
raise SystemExit(0 if report["all_command_exits_zero"] else 1)
