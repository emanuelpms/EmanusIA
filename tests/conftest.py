import os

import pytest

from emanusia import config
from emanusia.tools import load_all


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EMANUSIA_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("EMANUSIA_WORKFLOWS", str(tmp_path / "wf"))
    monkeypatch.setenv("EMANUSIA_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("EMANUSIA_NO_SCHEDULER", "1")
    load_all()


@pytest.fixture
def headless(monkeypatch):
    monkeypatch.setenv("EMANUSIA_HEADLESS", "1")
    monkeypatch.setattr(config, "HEADLESS", True)
    if os.path.exists("/opt/pw-browsers/chromium"):
        monkeypatch.setenv("EMANUSIA_CHROMIUM", "/opt/pw-browsers/chromium")
