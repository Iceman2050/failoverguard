import copy
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def config():
    return load("helpdesk_routes.json")


@pytest.fixture()
def broken_config():
    return load("broken_routes.json")


@pytest.fixture()
def policy():
    return load("helpdesk_policy.json")


@pytest.fixture()
def config_copy(config):
    return copy.deepcopy(config)
