"""Run the backend quality checks in the existing test environment."""

import subprocess
import sys


def main() -> int:
    # Format application code; lint tests/migrations too without reformatting
    # historical files. Type checks start with application code, not test fixtures.
    commands = (
        ("ruff", "format", "--check", "src"),
        ("ruff", "check", "src", "tests", "alembic"),
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
