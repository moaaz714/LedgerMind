"""Smoke test: the package skeleton is importable and self-describing.

The expected package list is written out rather than discovered by walking the
directory tree. A discovered list can only confirm that whatever exists imports --
it cannot fail when a package is *missing*, which is the drift worth catching.
Written out, this test also asserts the structure still matches plan.md.
"""

import importlib

import pytest

EXPECTED_PACKAGES = (
    "ledgermind",
    "ledgermind.agent",
    "ledgermind.app",
    "ledgermind.data",
    "ledgermind.eval",
    "ledgermind.guardrail",
    "ledgermind.llm",
    "ledgermind.tools",
)


@pytest.mark.parametrize("name", EXPECTED_PACKAGES)
def test_package_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("name", EXPECTED_PACKAGES)
def test_package_documents_its_responsibility(name):
    """Every package boundary here maps to a rule in the constitution.

    The docstring is where that mapping is visible to someone reading the code
    instead of the specs, which is the person most likely to break a boundary.
    """
    module = importlib.import_module(name)
    assert module.__doc__ and module.__doc__.strip(), f"{name} has no docstring"
