"""Compatibility helpers for the in-repo orchestrator namespace split."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def alias_orchestrator_module(legacy_name: str, target_name: str) -> ModuleType:
    module = importlib.import_module(target_name)
    sys.modules[legacy_name] = module
    return module
