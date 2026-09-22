"""jgrep's view of the JevKit runtime: which providers it offers, and its names for the shared pieces."""

from jevkit_core import (
    AnswerStore as Cache,
    Backend,
    Client as Jev,
    JevBudgetExceeded,
    JevError,
    JevFatal,
    Meter,
    Settings,
    catalog,
    resolve,
)


PROVIDERS = catalog("typesafe", "openrouter", "gateway")


def resolve_backend(name=None, *, model=None, require_key=True):
    return resolve(PROVIDERS, name, model=model, require_key=require_key)


__all__ = [
    "Backend",
    "Cache",
    "Jev",
    "JevBudgetExceeded",
    "JevError",
    "JevFatal",
    "Meter",
    "PROVIDERS",
    "Settings",
    "resolve_backend",
]
