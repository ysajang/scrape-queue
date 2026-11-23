"""Conventions from docs/development.md, enforced instead of only written down.

db/repo.py is the only module that writes to the job tables, so every state
change goes through the state machine. A write elsewhere skips transition
checks and leaves the job in a state the machine never allowed.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "scrapequeue"
JOB_TABLES = {"Job", "JobPage", "FailedPage", "DeadLetter"}
WRITE_FUNCS = {"insert", "update", "delete"}
ALLOWED = {PACKAGE / "db" / "repo.py", PACKAGE / "db" / "tables.py"}


def _source_files() -> list[Path]:
    return [p for p in PACKAGE.rglob("*.py") if "migrations" not in p.parts and p not in ALLOWED]


def _violations(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr == "state":
                found.append((node.lineno, "assigns .state"))

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in JOB_TABLES:
                found.append((node.lineno, f"constructs {name}"))
            if (
                name in WRITE_FUNCS
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in JOB_TABLES
            ):
                found.append((node.lineno, f"{name}({node.args[0].id})"))
    return found


def test_detector_catches_known_writes() -> None:
    sample = ast.parse(
        "job.state = 'done'\n"
        "session.add(Job(id=1))\n"
        "session.execute(update(JobPage).values(state='x'))\n"
    )
    kinds = {msg for _, msg in _violations(sample)}
    assert kinds == {"assigns .state", "constructs Job", "update(JobPage)"}


def test_only_repo_writes_job_tables() -> None:
    problems = []
    for path in _source_files():
        for lineno, msg in _violations(ast.parse(path.read_text(encoding="utf-8"))):
            problems.append(f"{path.relative_to(PACKAGE.parent)}:{lineno} {msg}")
    assert not problems, (
        "Job tables are written outside db/repo.py. Add a function to db/repo.py "
        "that goes through repo.transition() and call it instead:\n" + "\n".join(problems)
    )
