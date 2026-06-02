"""Shared path normalization — no planman imports to avoid cycles."""

import os


def normalize_path(path):
    """Normalize a file path for consistent comparison.

    Expands ~, resolves symlinks, returns absolute path.
    """
    if not path:
        return path
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (ValueError, OSError):
        return path
