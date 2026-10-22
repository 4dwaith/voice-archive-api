"""Keep the pure logic package independent of implementation layers."""

import ast
from pathlib import Path

import voice_archive_api.logic


def test_logic_does_not_import_implementation_layers():
    root = Path(voice_archive_api.logic.__file__).parent
    forbidden = (
        "sqlalchemy",
        "psycopg",
        "voice_archive_api.api",
        "voice_archive_api.db",
        "voice_archive_api.services",
        "voice_archive_api.file_handling",
        "voice_archive_api.core.config",
    )
    violations = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            for name in names:
                if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}: {name}")
    assert not violations, "\n".join(violations)
