"""The AI services behind the gateway, loaded from `services.yaml`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Paths the gateway answers itself; no service may claim them.
RESERVED = ("/health", "/ready", "/v1/models", "/v1/services", "/docs", "/redoc",
            "/openapi.json")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ConfigError(ValueError):
    """The service registry is invalid."""


@dataclass(frozen=True)
class Service:
    """One AI service: its worker's base URL and the public paths forwarded to it.

    A public route serves its own path and everything below it. It is sent to
    the same path on the worker unless `rewrites` maps it to another one, for
    example `/v1/embeddings/tokenize` to TEI's `/tokenize`.
    """

    name: str
    url: str
    routes: tuple[str, ...]
    description: str = ""
    # Worker paths the gateway probes: readiness (200 = ready) and its models,
    # either an OpenAI-style list or a TEI-style `/info` object.
    ready_path: str = "/ready"
    models_path: str = "/v1/models"
    # The worker's OpenAPI document; its public routes appear in the gateway's /docs.
    openapi_path: str = "/openapi.json"
    rewrites: Mapping[str, str] = field(default_factory=dict)

    def _route(self, path: str) -> str | None:
        matches = [route for route in self.routes
                   if path == route or path.startswith(route.rstrip("/") + "/")]
        return max(matches, key=len, default=None)

    def match(self, path: str) -> int:
        """Length of the longest route serving `path`, or 0 if none does."""

        route = self._route(path)
        return len(route) if route else 0

    def target(self, path: str) -> str:
        """The worker path for a public `path` this service serves."""

        route = self._route(path)
        if route is None:
            return path
        return self.rewrites.get(route, route) + path[len(route):]

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "description": self.description, "routes": list(self.routes)}


def _claims(route: str, other: str) -> bool:
    return route == other or other.startswith(route.rstrip("/") + "/")


def load_services(path: str | Path, env: Mapping[str, str]) -> list[Service]:
    """Services from a registry file, with environment overrides applied.

    Args:
        path: The YAML registry (see `services.yaml`).
        env: Environment; `AI_SERVICES` (comma-separated names) limits the
            enabled services, `AI_SERVICE_<NAME>_URL` overrides a URL.

    Returns:
        The enabled services in file order.

    Raises:
        ConfigError: The file is malformed, a name or route is invalid, two
            services claim the same route, a route covers a gateway path, or
            `AI_SERVICES` names an unknown service.
    """

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    specs = data.get("services")
    if not isinstance(specs, dict) or not specs:
        raise ConfigError(f"{path}: `services` must map service names to settings")
    wanted = [name.strip() for name in env.get("AI_SERVICES", "").split(",") if name.strip()]
    unknown = sorted(set(wanted) - set(specs))
    if unknown:
        raise ConfigError(f"AI_SERVICES names unknown services: {', '.join(unknown)}")

    services: list[Service] = []
    for name, spec in specs.items():
        if not _NAME.match(str(name)):
            raise ConfigError(f"invalid service name {name!r} (use a-z, 0-9 and _)")
        if wanted and name not in wanted:
            continue
        if not isinstance(spec, dict):
            raise ConfigError(f"service {name}: settings must be a mapping")
        url = env.get(f"AI_SERVICE_{name.upper()}_URL") or spec.get("url")
        routes, rewrites = _routes(name, spec.get("routes"))
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ConfigError(f"service {name}: `url` must be an http(s) URL")
        for route in routes:
            if any(_claims(route, reserved) or _claims(reserved, route) for reserved in RESERVED):
                raise ConfigError(f"service {name}: route {route} overlaps a gateway path")
        probes = {key: spec.get(key, default) for key, default in
                  (("ready", "/ready"), ("models", "/v1/models"), ("openapi", "/openapi.json"))}
        for key, value in probes.items():
            if not isinstance(value, str) or not value.startswith("/"):
                raise ConfigError(f"service {name}: `{key}` must be a path like /health")
        services.append(Service(name, url.rstrip("/"), tuple(routes),
                                description=str(spec.get("description") or ""),
                                ready_path=probes["ready"], models_path=probes["models"],
                                openapi_path=probes["openapi"], rewrites=rewrites))

    for index, service in enumerate(services):
        for other in services[index + 1:]:
            for route in service.routes:
                for other_route in other.routes:
                    if route == other_route:
                        raise ConfigError(f"route {route} is claimed by {service.name} "
                                          f"and {other.name}")
    return services


def _routes(name: str, items: object) -> tuple[list[str], dict[str, str]]:
    """Public routes and their worker paths from a `routes` list.

    Each item is a path forwarded unchanged (`/v1/embeddings`) or a one-entry
    mapping from the public path to the worker path (`/v1/embeddings/info: /info`).

    Raises:
        ConfigError: The list is empty or an item is not a path or such a mapping.
    """

    def is_path(value: object) -> bool:
        return isinstance(value, str) and value.startswith("/") and len(value) > 1

    if not isinstance(items, list) or not items:
        raise ConfigError(f"service {name}: `routes` must be a list of paths like /v1/x")
    routes, rewrites = [], {}
    for item in items:
        if isinstance(item, dict) and len(item) == 1:
            (public, worker), = item.items()
            if is_path(public) and isinstance(worker, str) and worker.startswith("/"):
                routes.append(public)
                rewrites[public] = worker.rstrip("/") or "/"
                continue
        elif is_path(item):
            routes.append(item)
            continue
        raise ConfigError(f"service {name}: route {item!r} must be a path like /v1/x "
                          "or a mapping `/public/path: /worker/path`")
    return routes, rewrites


def find(services: list[Service], path: str) -> Service | None:
    """The service whose longest matching route serves `path`."""

    best, length = None, 0
    for service in services:
        matched = service.match(path)
        if matched > length:
            best, length = service, matched
    return best
