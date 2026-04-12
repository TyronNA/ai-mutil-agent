"""AST-aware JavaScript patch helpers.

Hybrid mode support:
- Keep fast textual patching for normal cases.
- When textual matching fails, use AST identity (import/function/class/variable)
  to locate the intended target node and replace by byte range.

This module is intentionally conservative: it only applies when there is a
single unambiguous match. Otherwise it returns no-op and lets callers fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class _PatchTarget:
    kind: str
    key: str


def apply_ast_patch(base: str, find: str, replace: str) -> tuple[bool, str, str]:
    """Apply a JS patch by AST identity.

    Returns:
        (applied, patched_content, reason)
    """
    try:
        import esprima  # type: ignore[import-not-found]
    except Exception:
        return False, base, "esprima not available"

    try:
        find_ast = esprima.parseScript(find, {"range": True, "tolerant": True})
    except Exception:
        return False, base, "find snippet is not parseable JS"

    target = _extract_target(find_ast)
    if not target:
        return False, base, "unsupported AST target"

    try:
        base_ast = esprima.parseScript(base, {"range": True, "tolerant": True})
    except Exception:
        return False, base, "base file is not parseable JS"

    matches = _locate_targets(base_ast, target)
    if len(matches) != 1:
        return False, base, f"ambiguous target count={len(matches)}"

    start, end = matches[0]
    patched = base[:start] + replace + base[end:]
    return True, patched, f"ast matched {target.kind}:{target.key}"


def _extract_target(ast_root) -> Optional[_PatchTarget]:
    body = getattr(ast_root, "body", None) or []
    if len(body) != 1:
        return None

    node = body[0]
    node_type = getattr(node, "type", "")

    if node_type == "ImportDeclaration":
        source = getattr(getattr(node, "source", None), "value", "")
        if source:
            return _PatchTarget(kind="import", key=str(source))
        return None

    if node_type == "FunctionDeclaration":
        name = getattr(getattr(node, "id", None), "name", "")
        if name:
            return _PatchTarget(kind="function", key=name)
        return None

    if node_type == "ClassDeclaration":
        name = getattr(getattr(node, "id", None), "name", "")
        if name:
            return _PatchTarget(kind="class", key=name)
        return None

    if node_type == "VariableDeclaration":
        decls = getattr(node, "declarations", None) or []
        if len(decls) != 1:
            return None
        ident = getattr(decls[0], "id", None)
        name = getattr(ident, "name", "")
        if name:
            return _PatchTarget(kind="var", key=name)

    return None


def _locate_targets(ast_root, target: _PatchTarget) -> list[tuple[int, int]]:
    body = getattr(ast_root, "body", None) or []
    out: list[tuple[int, int]] = []

    for node in body:
        node_type = getattr(node, "type", "")
        rng = getattr(node, "range", None)
        if not isinstance(rng, list) or len(rng) != 2:
            continue

        if target.kind == "import" and node_type == "ImportDeclaration":
            source = getattr(getattr(node, "source", None), "value", "")
            if str(source) == target.key:
                out.append((int(rng[0]), int(rng[1])))

        elif target.kind == "function" and node_type == "FunctionDeclaration":
            name = getattr(getattr(node, "id", None), "name", "")
            if name == target.key:
                out.append((int(rng[0]), int(rng[1])))

        elif target.kind == "class" and node_type == "ClassDeclaration":
            name = getattr(getattr(node, "id", None), "name", "")
            if name == target.key:
                out.append((int(rng[0]), int(rng[1])))

        elif target.kind == "var" and node_type == "VariableDeclaration":
            decls = getattr(node, "declarations", None) or []
            if len(decls) != 1:
                continue
            ident = getattr(decls[0], "id", None)
            name = getattr(ident, "name", "")
            if name == target.key:
                out.append((int(rng[0]), int(rng[1])))

    return out