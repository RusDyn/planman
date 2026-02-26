"""Cross-platform hook launcher.

Resolves the Python executable so hooks work on Windows where
`python3` may not exist but `python` does.
"""

import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    script = sys.argv[1]
    result = subprocess.run(
        [sys.executable, script],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
