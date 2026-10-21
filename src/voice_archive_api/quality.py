"""Run the backend quality checks in the existing test environment."""

import subprocess
import sys


def main() -> int:
    # Format and lint all Python files; enforce strict types on application code.
    commands = (
        ("ruff", "format", "--check", "."),
        ("ruff", "check", "."),
        ("mypy",),
        (
            "pytest",
            "--cov=voice_archive_api",
            "--cov-report=term-missing",
            "--cov-report=html:reports/htmlcov",
            "--cov-report=xml:reports/coverage.xml",
        ),
    )
    for command in commands:
        print(f"Running {' '.join(command)}", flush=True)
        result = subprocess.run([sys.executable, "-m", *command], check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
