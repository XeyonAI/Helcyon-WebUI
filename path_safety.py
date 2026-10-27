"""Filesystem names that come from a request: one rule set for every route.

Two layers, both pure (no Flask) so route modules and tests can use them:

* ``is_plain_component(name)``: the shape every client-supplied file or folder
  name must have. One path segment, never a path: no ``/`` or ``\\`` (Windows
  treats both as separators), no ``:`` (drive letters and NTFS alternate data
  streams), no NUL, not ``.`` or ``..``, no leading or trailing whitespace.
  Dots inside a name are fine ("Hey babe.. ok.txt" is a real chat title).
* ``safe_join(base, *parts)``: joins plain components under ``base`` and then
  re-checks the *resolved* path is still inside ``base``, which is what catches
  a symlink or junction pointing elsewhere. Raises ``UnsafePathError``; it never
  returns a path outside ``base``.

``document_tools.resolve_document_path`` is the model-facing equivalent for the
document roots; this module is the HTTP-facing one.
"""
import os

MAX_COMPONENT_LENGTH = 255
_FORBIDDEN_CHARACTERS = frozenset('/\\:\0')


class UnsafePathError(ValueError):
    """A request named a path outside the folder it is allowed to use."""


def is_plain_component(name, max_length=MAX_COMPONENT_LENGTH):
    """True when ``name`` is a single, ordinary file or folder name."""
    if not isinstance(name, str) or not name or len(name) > max_length:
        return False
    if name != name.strip() or name in (".", ".."):
        return False
    return not any(ch in _FORBIDDEN_CHARACTERS for ch in name)


def is_relative_subpath(value, max_length=1024):
    """True for "a/b/c.txt": forward-slash segments, each a plain component."""
    if not isinstance(value, str) or not value or len(value) > max_length:
        return False
    return all(is_plain_component(part) for part in value.split("/"))


def is_within(base, candidate):
    """True when ``candidate`` resolves to ``base`` or somewhere beneath it."""
    real_base = os.path.realpath(base)
    real_candidate = os.path.realpath(candidate)
    if os.name == "nt":
        real_base, real_candidate = os.path.normcase(real_base), os.path.normcase(real_candidate)
    return real_candidate == real_base or real_candidate.startswith(real_base.rstrip(os.sep) + os.sep)


def safe_join(base, *parts):
    """``os.path.join(base, *parts)`` for request-supplied parts, or raise."""
    if not parts:
        raise UnsafePathError("No file name was given.")
    for part in parts:
        if not is_plain_component(part):
            raise UnsafePathError("That file name is not allowed.")
    candidate = os.path.join(base, *parts)
    if not is_within(base, candidate):
        raise UnsafePathError("That file name points outside its folder.")
    return candidate
