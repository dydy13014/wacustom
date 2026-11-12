"""Garde-fou de l'authentification admin.

Deux controles :
- analyse du code : chaque route /admin/api/* verifie le jeton admin AVEC `await`,
  et aucun appel de `_verify_admin_token` n'est fait sans `await` ;
- appel HTTP reel sans cookie sur une route sans corps : 401 attendu.
"""
import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wastream.api import routes

SRC = Path(routes.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)

# Routes /admin/api/* volontairement sans jeton admin (aucune a ce jour).
PUBLIC_ADMIN_API: set[str] = set()


def _route_path(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"
                and dec.args and isinstance(dec.args[0], ast.Constant)):
            return dec.args[0].value
    return None


def _is_verify_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_verify_admin_token")


def _admin_api_handlers():
    return [(fn, _route_path(fn)) for fn in ast.walk(TREE)
            if isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef))
            and (_route_path(fn) or "").startswith("/admin/api/")]


def test_il_y_a_des_routes_admin_api():
    assert len(_admin_api_handlers()) > 20


def test_aucun_appel_sans_await():
    awaited = {id(n.value) for n in ast.walk(TREE) if isinstance(n, ast.Await)}
    bad = [n.lineno for n in ast.walk(TREE) if _is_verify_call(n) and id(n) not in awaited]
    assert not bad, f"_verify_admin_token doit etre attendue (await) : lignes {bad}"


def test_chaque_route_admin_api_verifie_le_jeton():
    manquantes = []
    for fn, path in _admin_api_handlers():
        if path in PUBLIC_ADMIN_API:
            continue
        verifie = any(isinstance(n, ast.Await) and _is_verify_call(n.value) for n in ast.walk(fn))
        if not verifie:
            manquantes.append(f"{path} ({fn.name}, ligne {fn.lineno})")
    assert not manquantes, "routes admin sans verification du jeton : " + ", ".join(manquantes)


def test_purge_du_cache_refuse_sans_cookie():
    app = FastAPI()
    app.include_router(routes.router)
    r = TestClient(app).delete("/admin/api/cache")
    assert r.status_code == 401
