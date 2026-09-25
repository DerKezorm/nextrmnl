"""No log call may carry a secret as a value.

The log holds who connected where and when, never what was typed and never a credential. This scan catches the
obvious mistakes in the code: a format string like ``password=%s``, an f-string with ``{token}`` in it, or an
argument whose name says what it is (``payload.password``, ``secret_text``, ``private_key``). Counts, ids and
booleans about secrets are fine (``added_passwords=%s``, ``has_passphrase``). A legitimate exception, should one
ever exist, goes into ``ALLOWED`` as the exact call text; today there is none.
"""

from __future__ import annotations

import ast
import re
from itertools import pairwise
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
KEYWORDS = {"password", "passphrase", "token", "secret"}
PLURALS = {"passwords", "passphrases", "tokens", "secrets", "private_keys"}
#: A component in front that turns a secret's name into a fact about it, and one at the end that makes it a count
#: or a reference.
FACT_PREFIXES = {"has", "is", "with", "without", "needs", "wants", "no"}
COUNT_SUFFIXES = {"count", "total", "len", "size", "id", "ids"}
#: Exact call texts (``ast.unparse`` of the call) that are allowed although the scan objects. Keep it empty.
ALLOWED: set[str] = set()
#: The app has at least this many log calls; fewer means the scan looked in the wrong place.
FLOOR = 40

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: ``label=%s``, ``label: %r``, ``label={x}`` in a format string.
LABEL_BEFORE_VALUE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(?:%|\{)")
NAMED_PLACEHOLDER = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)")


def names_a_secret(identifier: str) -> bool:
    """Whether an identifier, by its components, is a secret rather than a fact or a count about one."""
    lowered = identifier.lower()
    if lowered in PLURALS:
        return True
    parts = lowered.split("_")
    if parts[0] in FACT_PREFIXES or parts[-1] in COUNT_SUFFIXES:
        return False
    if any(part in KEYWORDS for part in parts):
        return True
    return any(a == "private" and b == "key" for a, b in pairwise(parts))


def is_log_call(node: ast.Call) -> bool:
    target = node.func
    if not isinstance(target, ast.Attribute) or target.attr not in LOG_METHODS:
        return False
    root = target.value
    if isinstance(root, ast.Call):
        return "getLogger" in ast.unparse(root.func)
    name = getattr(root, "id", "") or getattr(root, "attr", "")
    return "log" in name.lower()


def offending_in_format(text: str) -> list[str]:
    labels = LABEL_BEFORE_VALUE.findall(text) + NAMED_PLACEHOLDER.findall(text)
    return [label for label in labels if names_a_secret(label)]


def offending_in_expression(source: str) -> list[str]:
    if source.startswith("len(") and source.endswith(")"):
        return []  # a length is never the secret
    return [name for name in IDENTIFIER.findall(source) if names_a_secret(name)]


def offending(node: ast.Call) -> list[str]:
    """What a log call gives away, by name. Empty when nothing."""
    arguments = list(node.args)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "log":
        arguments = arguments[1:]
    found: list[str] = []
    for index, argument in enumerate(arguments):
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            found += offending_in_format(argument.value)
        elif isinstance(argument, ast.JoinedStr):
            for part in argument.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    found += offending_in_format(part.value)
                elif isinstance(part, ast.FormattedValue):
                    found += offending_in_expression(ast.unparse(part.value))
        elif index > 0:
            found += offending_in_expression(ast.unparse(argument))
    for keyword in node.keywords:
        if keyword.arg not in ("exc_info", "stack_info", "stacklevel"):
            found += offending_in_expression(ast.unparse(keyword.value))
    return list(dict.fromkeys(found))


def scan(source: str, origin: str = "<snippet>") -> list[tuple[str, int, str, list[str]]]:
    tree = ast.parse(source, filename=origin)
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and is_log_call(node):
            results.append((origin, node.lineno, ast.unparse(node), offending(node)))
    return results


def test_no_log_call_carries_a_secret() -> None:
    calls = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" not in path.parts:
            calls += scan(path.read_text(encoding="utf-8"), str(path.relative_to(APP.parent)))
    assert len(calls) >= FLOOR, f"only {len(calls)} log calls found; is the scan looking at the app?"
    bad = [
        f"{origin}:{line}: {', '.join(names)} in {text}"
        for origin, line, text, names in calls
        if names and text not in ALLOWED
    ]
    assert not bad, "Secrets in log calls:\n" + "\n".join(bad)
    stale = ALLOWED - {text for _origin, _line, text, _names in calls}
    assert not stale, f"ALLOWED names calls that no longer exist: {stale}"


def test_the_scan_knows_a_secret_when_it_sees_one() -> None:
    assert scan('logger.info("pw=%s", password)')[0][3] == ["password"]
    assert scan('logger.info("password=%s", value)')[0][3] == ["password"]
    assert scan('logger.warning("token: %r", request.headers)')[0][3] == ["token"]
    assert scan('logger.debug(f"key {private_key} loaded")')[0][3] == ["private_key"]
    assert scan('logger.debug("%(secret)s", {"secret": s})')[0][3] == ["secret"]
    assert scan('logger.info("Stored", extra={"passphrase": p})')[0][3] == ["passphrase"]
    assert scan('logger.info("Sign-in name=%s", payload.password)')[0][3] == ["password"]
    assert scan('logger.info("Client secret_key=%s", x)')[0][3] == ["secret_key"]
    assert scan('logger.info("%s", row.private_key_enc)')[0][3] == ["private_key_enc"]
    assert scan('logger.info("%s", token_hash)')[0][3] == ["token_hash"]
    assert scan('logging.getLogger("x").info("%s", passwords)')[0][3] == ["passwords"]


def test_the_scan_lets_facts_and_counts_through() -> None:
    assert scan('logger.info("Password stored account=%s connection_id=%s", account.name, connection_id)')[0][3] == []
    assert scan('logger.info("Vault imported added_passwords=%s skipped_keys=%s", a, b)')[0][3] == []
    assert scan('logger.info("Key added has_passphrase=%s", key.has_passphrase)')[0][3] == []
    assert scan('logger.info("Sign-in failed for unknown account %r", name)')[0][3] == []
    assert scan('logger.info("%s keys", len(payload.keys))')[0][3] == []
    assert scan('logger.info("key_count=%s password_count=%s", len(k), len(payload.passwords))')[0][3] == []
    assert scan('logger.info("token_id=%s", token_id)')[0][3] == []
    assert scan('logger.exception("Unhandled error on %s %s", method, path)')[0][3] == []
    assert scan('print("password=%s" % password)') == [], "not a log call"
