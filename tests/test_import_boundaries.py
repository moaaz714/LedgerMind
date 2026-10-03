"""Architectural rules, enforced rather than documented (T015).

Two constitutional rules are structural: no module in the decision path may reach ground
truth (Article III), and the deterministic layer is stdlib only, with pandas confined to
the presentation edge (plan.md). Both are the kind of rule that dies quietly -- a
convenience import while debugging, never reverted -- so neither is left to discipline.

The check reads each module's syntax tree rather than grepping the text, so prose in
comments and docstrings explaining these rules does not trip it. Only real code counts.
"""

import ast
import pathlib

import pytest

PACKAGE_ROOT = pathlib.Path("ledgermind")

# Packages that participate in producing a decision. Ground truth is the answer key; a
# module here reading it would be marking its own homework.
DECISION_PATH = ("tools", "agent", "guardrail", "app")

# pandas is permitted only where Streamlit needs a DataFrame to render a table. Keeping the
# deterministic layer stdlib-only is what makes Article IV's purity rule trivial to honour
# and lets tool tests use hand-computed fixtures.
STDLIB_ONLY = ("tools", "data", "agent", "guardrail", "eval")
FORBIDDEN_THIRD_PARTY = ("pandas", "numpy", "pyarrow", "altair", "streamlit")


def _modules(*package_names):
    found = []
    for name in package_names:
        directory = PACKAGE_ROOT / name
        if directory.exists():
            found.extend(sorted(directory.rglob("*.py")))
    return found


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _non_docstring_strings(tree):
    """Every string constant except docstrings.

    Docstrings are excluded because these modules legitimately *discuss* ground truth in
    their documentation. Comments never appear in the tree at all.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize("path", _modules(*DECISION_PATH), ids=str)
def test_decision_path_does_not_reach_ground_truth(path):
    """Article III: only tests/ and eval/ may read truth.json."""
    tree = _parse(path)

    for name in _imported_names(tree):
        assert "gen" not in name.split("."), (
            f"{path} imports the generator; the decision path must not depend on "
            "generation code, which is where ground truth is written"
        )

    for value in _non_docstring_strings(tree):
        assert "truth" not in value.lower(), (
            f"{path} contains the string literal {value!r}; ground truth is readable only "
            "by tests/ and eval/ (Article III)"
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "TRUTH_FILENAME", f"{path} references TRUTH_FILENAME"


@pytest.mark.parametrize("path", _modules(*STDLIB_ONLY), ids=str)
def test_deterministic_layer_is_stdlib_only(path):
    for name in _imported_names(path and _parse(path)):
        root = name.split(".")[0]
        assert root not in FORBIDDEN_THIRD_PARTY, (
            f"{path} imports {root}; the deterministic layer is stdlib only, and "
            f"{root} belongs at the presentation edge"
        )


def test_the_boundary_test_actually_has_teeth(tmp_path):
    """Mutation check: a module that breaks either rule must fail these assertions.

    Without this, a bug in the AST walk would leave both rules silently unenforced -- the
    worst possible outcome for a test whose whole purpose is enforcement.
    """
    offender = tmp_path / "offender.py"
    offender.write_text(
        '"""A docstring mentioning truth.json, which must NOT trip the check."""\n'
        "import pandas\n"
        'TRUTH = "truth.json"\n',
        encoding="utf-8",
    )
    tree = _parse(offender)

    assert any(name == "pandas" for name in _imported_names(tree))
    assert any("truth" in value.lower() for value in _non_docstring_strings(tree))
    # And the docstring itself was correctly excluded.
    assert not any("must NOT trip" in value for value in _non_docstring_strings(tree))


def test_tools_are_pure_of_file_access():
    """Article IV: no I/O in the tools.

    Reading a file inside a tool would make its result depend on something other than its
    arguments, and the hand-computed fixtures could no longer pin its behaviour.
    """
    for path in _modules("tools"):
        tree = _parse(path)
        for name in _imported_names(tree):
            root = name.split(".")[0]
            assert root not in {"csv", "json", "pathlib", "io", "os", "urllib", "socket"}, (
                f"{path} imports {root}; tools take plain values and return plain values"
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", f"{path} calls open()"
