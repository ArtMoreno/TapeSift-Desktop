"""Marketing copy and product writing stay aligned with the app."""

from __future__ import annotations

import ast
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class _Parser(HTMLParser):
    pass


def test_readme_documents_both_platforms_and_the_production_launcher():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for value in ("Windows", "Linux", "Omarchy", "python -m tapesift", "First Read"):
        assert value in readme


def _copy_strings(path: Path) -> list[str]:
    """Every string literal in a module except its docstrings.

    The em dash rule is about prose the user reads, so it is applied to
    string literals - labels, messages, tooltips - and deliberately not to
    comments or docstrings, which are writing for whoever edits the file.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                docstrings.add(id(node.body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_app_and_website_copy_contains_no_em_dashes():
    offenders = []
    checked = []
    for root in (ROOT / "tapesift" / "ui", ROOT / "tapesift" / "ui_v2"):
        for path in sorted(root.rglob("*.py")):
            checked.append(path)
            for value in _copy_strings(path):
                # A standalone missing-value glyph is not prose punctuation.
                if "—" in value and value.strip() != "—":
                    offenders.append(f"{path}: {value.strip()[:60]}")
    # The website is copy end to end, so it is checked whole.
    for path in sorted((ROOT / "website").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}:
            checked.append(path)
            if "—" in path.read_text(encoding="utf-8"):
                offenders.append(str(path))
    assert checked
    # Every offender at once. Asserting inside the loop stopped at the first
    # file and hid the rest, so CI reported one violation, the fix went in,
    # and the next run reported the next one.
    assert not offenders, "em dashes in copy:\n" + "\n".join(offenders)
