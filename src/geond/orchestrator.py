"""Compatibility module alias for :mod:`geond_orchestrator.orchestrator`."""

from geond._orchestrator_compat import alias_orchestrator_module

_module = alias_orchestrator_module(__name__, "geond_orchestrator.orchestrator")
globals().update(_module.__dict__)
