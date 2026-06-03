from __future__ import annotations

import inspect
from pathlib import Path


def test_app_import_context_uses_third_party_requests() -> None:
    import requests

    requests_path = Path(requests.__file__).as_posix()

    assert "src/requests" not in requests_path
    assert any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in inspect.signature(requests.get).parameters.values()
    )
