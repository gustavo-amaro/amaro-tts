"""Locale catalogs stay in sync and cover every error code the backend can raise."""
import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "app" / "static" / "locales"
PLURAL_FORMS = {"zero", "one", "two", "few", "many", "other"}


def load(name: str) -> dict:
    return json.loads((LOCALES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def keys(node: dict, prefix: str = "") -> set[str]:
    """Flattened message keys; plural objects ({one, other, ...}) count as a single message."""
    out = set()
    for k, v in node.items():
        if isinstance(v, dict) and not set(v) <= PLURAL_FORMS:
            out |= keys(v, f"{prefix}{k}.")
        else:
            out.add(prefix + k)
    return out


def placeholders(node: dict, prefix: str = "") -> dict[str, set[str]]:
    out = {}
    for k, v in node.items():
        if isinstance(v, dict) and not set(v) <= PLURAL_FORMS:
            out |= placeholders(v, f"{prefix}{k}.")
        else:
            texts = v.values() if isinstance(v, dict) else [v]
            out[prefix + k] = {p for text in texts for p in re.findall(r"\{(\w+)\}", text)}
    return out


def raised_error_codes() -> set[str]:
    """First argument of every NotFound/Conflict/InvalidInput(...) call in app/."""
    codes = set()
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"NotFound", "Conflict", "InvalidInput"}):
                codes.add(node.args[0].value)
    return codes


LOCALES = sorted(p.stem for p in LOCALES_DIR.glob("*.json"))


def test_english_and_portuguese_exist():
    assert {"en", "pt-BR"} <= set(LOCALES)


@pytest.mark.parametrize("locale", [name for name in LOCALES if name != "en"])
def test_locale_has_same_keys_and_placeholders_as_english(locale):
    en, other = load("en"), load(locale)
    assert keys(other) == keys(en)
    assert placeholders(other) == placeholders(en)


def test_every_backend_error_code_is_translated():
    codes = raised_error_codes()
    assert codes  # the AST scan found the raise sites
    assert codes <= set(load("en")["errors"])
