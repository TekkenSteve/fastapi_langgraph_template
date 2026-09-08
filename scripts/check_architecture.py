"""Architecture consistency check: enforce the layering from AGENTS.md.

Statically walks src/agent_server imports and fails on forbidden layer edges:

    app ──→ controller/http ──→ usecase ──→ repo ──→ domain
                      │            │
                      └──→ auth ───┘      config/infra ← leaves, any layer may use

Run: uv run python scripts/check_architecture.py
"""

import ast
import sys
from pathlib import Path

LAYERS = ("app", "config", "domain", "repo", "usecase", "controller", "auth", "infra")

# layer -> layers it must NOT import (checked against reality; adding a rule
# here means the current codebase already satisfies it)
FORBIDDEN: dict[str, frozenset[str]] = {
    "app": frozenset(),  # composition root may wire anything
    "config": frozenset({"app", "domain", "repo", "usecase", "controller", "auth", "infra"}),
    "domain": frozenset({"app", "repo", "usecase", "controller", "auth", "infra"}),
    "infra": frozenset({"app", "domain", "repo", "usecase", "controller", "auth"}),
    "repo": frozenset({"app", "usecase", "controller"}),
    "usecase": frozenset({"app", "controller"}),
    "controller": frozenset({"app"}),
    "auth": frozenset({"app", "controller"}),
}


def _layer_of(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) > 1 and parts[0] == "agent_server" and parts[1] in LAYERS:
        return parts[1]
    return None


def main() -> int:
    violations: list[str] = []
    root = Path("src/agent_server")
    for file in sorted(root.rglob("*.py")):
        if "__pycache__" in str(file):
            continue
        src_module = "agent_server." + str(file.relative_to(root)).replace("/", ".").removesuffix(".py")
        src_layer = _layer_of(src_module)
        if not src_layer:
            continue
        tree = ast.parse(file.read_text(), filename=str(file))
        for node in ast.walk(tree):
            targets: list[tuple[str, int]] = []
            if isinstance(node, ast.Import):
                targets = [(a.name, node.lineno) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                targets = [(node.module, node.lineno)]
            for module, lineno in targets:
                dst_layer = _layer_of(module)
                if dst_layer and dst_layer != src_layer and dst_layer in FORBIDDEN[src_layer]:
                    violations.append(f"{file}:{lineno}: {src_layer} must not import {dst_layer} ({module})")

    if violations:
        print("Layering violations (see AGENTS.md):")
        for v in violations:
            print(f"  {v}")
        return 1
    print(f"Architecture OK — {len(list(root.rglob('*.py')))} files checked, layering rules hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
