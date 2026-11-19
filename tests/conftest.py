import json
from pathlib import Path

import pytest
import requests

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", headers=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class ScriptedSession:
    """Returns scripted items in order; an Exception instance is raised."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append((url, headers, data, timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def robots_text():
    return (FIXTURES / "robots.txt").read_text()


def load(name):
    return json.loads((FIXTURES / name).read_text())


TIMEOUT = requests.exceptions.Timeout("slow")
CONNERR = requests.exceptions.ConnectionError("down")
