"""Resolve the Codex CLI executable consistently across application features."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping


class CodexExecutableResolutionError(RuntimeError):
    """Raised when no safe, executable Codex CLI candidate is available."""


ExecutableLookup = Callable[[str], str | None]


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_codex_executable(
    *,
    environ: Mapping[str, str] | None = None,
    which: ExecutableLookup | None = None,
) -> str:
    """Select Codex from an override, the user-local bin, or normal PATH."""

    active_environ = os.environ if environ is None else environ
    path_lookup = shutil.which if which is None else which

    override = active_environ.get("CODEX_EXECUTABLE")
    if override is not None:
        if _is_executable_file(override):
            return override
        raise CodexExecutableResolutionError("Codex executable is unavailable")

    home = active_environ.get("HOME")
    if home:
        user_local = os.path.join(home, ".local", "bin", "codex")
        if _is_executable_file(user_local):
            return user_local

    executable = path_lookup("codex")
    if executable is None:
        raise CodexExecutableResolutionError("Codex executable is unavailable")
    return executable
