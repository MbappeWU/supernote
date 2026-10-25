"""
.. include:: ../README.md
"""

import importlib
from typing import Any

__all__ = [
    "client",
    "models",
    "notebook",
    "server",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
