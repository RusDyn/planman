"""Clear planman session state files cross-platform."""

import glob
import os
import sys
import tempfile


def clear():
    """Remove all planman state files. Returns (removed_count, total_count)."""
    pattern = os.path.join(tempfile.gettempdir(), "planman-*.json")
    files = glob.glob(pattern)
    removed = 0
    for f in files:
        try:
            os.remove(f)
            removed += 1
        except OSError:
            pass
    return removed, len(files)


def list_sessions():
    """Return list of active planman state files."""
    pattern = os.path.join(tempfile.gettempdir(), "planman-*.json")
    return glob.glob(pattern)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        files = list_sessions()
        print("\n".join(files) if files else "No active sessions")
    else:
        removed, total = clear()
        print(f"Planman state cleared. Removed {removed}/{total} files.")
