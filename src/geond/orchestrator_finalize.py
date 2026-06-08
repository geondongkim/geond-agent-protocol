"""Compatibility module alias for :mod:`geond_orchestrator.orchestrator_finalize`."""

from geond._orchestrator_compat import alias_orchestrator_module

_module = alias_orchestrator_module(__name__, "geond_orchestrator.orchestrator_finalize")
globals().update(_module.__dict__)
