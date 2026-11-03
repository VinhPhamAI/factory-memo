"""One OpenAPI document for the gateway and the public routes of every service.

The gateway's own endpoints come from FastAPI. Each worker publishes its own
OpenAPI document; from it, only the operations under the service's public
routes are kept, at their public paths (TEI's `/tokenize` becomes
`/v1/embeddings/tokenize`), tagged with the service name. Worker-only paths
such as `/health` or TEI's `/embed` are left out, so `/docs` lists exactly what
the gateway forwards.
"""

from __future__ import annotations

import copy
from typing import Any

from .registry import Service

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_REF_PREFIX = "#/components/"


def public_operations(service: Service, spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The worker's path items that the gateway serves, keyed by public path.

    Args:
        service: The service; its routes and rewrites map worker paths to public ones.
        spec: The worker's OpenAPI document.

    Returns:
        Copies of the matching path items, with each operation tagged with the
        service name and its `operationId` prefixed by it.
    """

    targets = [(service.rewrites.get(route, route), route) for route in service.routes]
    paths: dict[str, dict[str, Any]] = {}
    for worker_path, item in (spec.get("paths") or {}).items():
        matches = [(target, route) for target, route in targets
                   if worker_path == target or worker_path.startswith(target.rstrip("/") + "/")]
        if not matches or not isinstance(item, dict):
            continue
        target, route = max(matches, key=lambda match: len(match[0]))
        item = copy.deepcopy(item)
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                operation["tags"] = [service.name]
                if operation.get("operationId"):
                    operation["operationId"] = f"{service.name}_{operation['operationId']}"
        paths[route + worker_path[len(target):]] = item
    return paths


def merge(base: dict[str, Any], specs: list[tuple[Service, dict[str, Any]]]) -> dict[str, Any]:
    """Adds the public operations of each service to the gateway's document.

    Only the components the kept operations reference are copied. A component
    whose name another document already uses with different content is renamed
    `<service>_<name>`, and its references are rewritten.

    Args:
        base: The gateway's own OpenAPI document; it is not modified.
        specs: Each reachable service with its worker's OpenAPI document.

    Returns:
        The merged document.
    """

    merged = copy.deepcopy(base)
    merged_paths = merged.setdefault("paths", {})
    merged_components = merged.setdefault("components", {})
    for service, spec in specs:
        paths = public_operations(service, spec)
        if not paths:
            continue
        components = spec.get("components") or {}
        used = _referenced(paths, components)
        renames: dict[str, str] = {}
        for kind, name in sorted(used):
            existing = merged_components.get(kind, {})
            original = components[kind][name]
            new = name if existing.get(name, original) == original else f"{service.name}_{name}"
            renames[f"{_REF_PREFIX}{kind}/{name}"] = f"{_REF_PREFIX}{kind}/{new}"
        for kind, name in used:
            new_name = renames[f"{_REF_PREFIX}{kind}/{name}"].rsplit("/", 1)[1]
            merged_components.setdefault(kind, {})[new_name] = _rewrite(
                components[kind][name], renames)
        for path, item in paths.items():
            merged_paths[path] = _rewrite(item, renames)
    return merged


def _referenced(node: Any, components: dict[str, Any]) -> set[tuple[str, str]]:
    """(kind, name) of every component `node` references, directly or through others."""

    found: set[tuple[str, str]] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            ref = current.get("$ref")
            if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
                kind, _, name = ref[len(_REF_PREFIX):].partition("/")
                if (kind, name) not in found and name in components.get(kind, {}):
                    found.add((kind, name))
                    pending.append(components[kind][name])
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found


def _rewrite(node: Any, renames: dict[str, str]) -> Any:
    """A copy of `node` with `$ref` values renamed."""

    if isinstance(node, dict):
        return {key: (renames.get(value, value) if key == "$ref" and isinstance(value, str)
                      else _rewrite(value, renames))
                for key, value in node.items()}
    if isinstance(node, list):
        return [_rewrite(value, renames) for value in node]
    return node
