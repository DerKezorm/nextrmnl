"""Log messages are English and stay so.

A mixed log is not searchable: whoever looks for "not reachable" misses the German half of the cases, and a
line pasted into a bug report or a web search must be readable to everyone. Comments, docstrings and the UI are
not affected.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
#: Words that do not exist in English and turn up in real messages. Deliberately without "in", "so" and other
#: doubles. Matched as whole words, case-insensitively.
GERMAN_WORDS = ("und", "nicht", "wird", "wurde", "der", "die", "das", "für", "mit", "ist", "kein", "keine", "wurden")
GERMAN = re.compile(r"(?<![\w%])(" + "|".join(GERMAN_WORDS) + r")(?![\w])", re.IGNORECASE)
UMLAUTS = re.compile(r"[äöüÄÖÜß]")
#: A run of the whole app has at least this many log calls; fewer means the scan looked in the wrong place.
FLOOR = 40


def is_log_call(node: ast.Call) -> bool:
    target = node.func
    if not isinstance(target, ast.Attribute) or target.attr not in LOG_METHODS:
        return False
    root = target.value
    if isinstance(root, ast.Call):
        return "getLogger" in ast.unparse(root.func)
    name = getattr(root, "id", "") or getattr(root, "attr", "")
    return "log" in name.lower()


def message_texts(node: ast.Call) -> list[str]:
    """Every fixed text a log call carries: the format string and constant arguments after it."""
    arguments = node.args[1:] if isinstance(node.func, ast.Attribute) and node.func.attr == "log" else node.args
    texts: list[str] = []
    for argument in arguments:
        branches = (argument.body, argument.orelse) if isinstance(argument, ast.IfExp) else (argument,)
        for branch in branches:
            if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                texts.append(branch.value)
            elif isinstance(branch, ast.JoinedStr):
                texts.append("".join(p.value for p in branch.values if isinstance(p, ast.Constant) and isinstance(p.value, str)))
    return texts


def messages() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and is_log_call(node):
                for text in message_texts(node):
                    found.append((str(path.relative_to(APP.parent)), node.lineno, text))
    return found


def offending(text: str) -> str | None:
    if UMLAUTS.search(text):
        return "umlaut"
    match = GERMAN.search(text)
    return match.group(1) if match else None


def test_every_log_message_is_english() -> None:
    found = messages()
    assert len(found) >= FLOOR, f"only {len(found)} log calls found; is the scan looking at the app?"
    bad = [f"{path}:{line}: {word!r} in {text!r}" for path, line, text in found if (word := offending(text))]
    assert not bad, "German in log messages:\n" + "\n".join(bad)


def test_the_scan_knows_german_when_it_sees_it() -> None:
    assert offending("Verbindung wird aufgebaut") == "wird"
    assert offending("Schlüssel geladen") == "umlaut"
    assert offending("Die Sitzung ist zu Ende") in {"die", "Die", "ist"}
    assert offending("Session ended target=%s") is None
    assert offending("Connection %s died: %s") is None, "'died' is not 'die'"
    assert offending("Added column %s.%s") is None


def test_the_scan_sees_calls_on_named_loggers_and_on_getlogger() -> None:
    tree = ast.parse(
        'logger.info("a")\n'
        'logging.getLogger("x").warning("b")\n'
        'self.log.debug("c")\n'
        'log.log(logging.INFO, "d")\n'
        'print("not a log call")\n'
    )
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and is_log_call(node)]
    assert [message_texts(call) for call in calls] == [["a"], ["b"], ["c"], ["d"]]
