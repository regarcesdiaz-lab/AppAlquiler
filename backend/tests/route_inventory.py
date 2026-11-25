"""Every route the app really serves, as (method, path), whatever FastAPI version.

Since FastAPI 0.162, ``app.routes`` holds lazy ``_IncludedRouter`` wrappers instead of the
``APIRoute`` objects of included routers, so ``isinstance(route, APIRoute)`` finds nothing.
This walks both shapes. An unknown route type fails loudly: a guard test built on an empty
inventory would pass without checking anything.
"""

from collections.abc import Iterable, Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Mount, Route

IGNORED_METHODS = frozenset({"HEAD"})  # added automatically next to every GET


class UnknownRouteTypeError(AssertionError):
    pass


def _methods(route: object) -> set[str]:
    return set(getattr(route, "methods", None) or ()) - IGNORED_METHODS


def _walk(routes: Iterable[object], prefix: str = "") -> Iterator[tuple[str, str]]:
    for route in routes:
        if hasattr(route, "effective_candidates"):  # FastAPI >= 0.162 included router
            yield from _walk(route.effective_candidates(), prefix)
        elif hasattr(route, "original_route"):  # its effective route context
            original = route.original_route
            starlette_route = getattr(route, "starlette_route", None)
            path = getattr(route, "path", "") or getattr(starlette_route, "path", "")
            if not path:
                raise UnknownRouteTypeError(f"Route without a path: {original!r}")
            for method in _methods(original):
                yield method, prefix + path
        elif isinstance(route, APIRoute | Route):
            for method in _methods(route):
                yield method, prefix + route.path
        elif isinstance(route, Mount):
            yield from _walk(route.routes, prefix + route.path)
        elif isinstance(route, BaseRoute):
            raise UnknownRouteTypeError(f"Unknown route type {type(route).__name__}")
        else:
            raise UnknownRouteTypeError(f"Not a route: {route!r}")


def app_routes(app: FastAPI) -> set[tuple[str, str]]:
    return set(_walk(app.routes))
