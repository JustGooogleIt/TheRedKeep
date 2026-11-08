import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def gamma_events():
    return json.loads((FIX / "gamma_events.json").read_text())


@pytest.fixture
def kalshi_events():
    return json.loads((FIX / "kalshi_events.json").read_text())["events"]
