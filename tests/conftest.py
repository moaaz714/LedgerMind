"""Shared fixtures.

The merchant set is generated once per session into a temporary directory rather than read
from `data/merchants/`, so the tests never depend on what happens to be lying around on
disk and never write into the working tree.
"""

import json

import pytest

from ledgermind.data import gen, load


@pytest.fixture(scope="session")
def merchant_root(tmp_path_factory):
    """The full 20-merchant set, generated once."""
    root = tmp_path_factory.mktemp("merchant_set")
    gen.generate_all(root)
    return root


@pytest.fixture(scope="session")
def merchants(merchant_root):
    """Loaded inputs plus ground truth, keyed by merchant id.

    Ground truth is read here because this is test code -- the only place other than the
    evaluation that Article III permits to read it.
    """
    loaded = {}
    for spec in gen.MERCHANT_SPECS:
        directory = merchant_root / spec.merchant_id
        loaded[spec.merchant_id] = {
            "spec": spec,
            "inputs": load.load_merchant(directory),
            "truth": json.loads((directory / gen.TRUTH_FILENAME).read_text(encoding="utf-8")),
        }
    return loaded


@pytest.fixture
def merchant(merchants):
    """Fetch one merchant by id."""
    return lambda merchant_id: merchants[merchant_id]
