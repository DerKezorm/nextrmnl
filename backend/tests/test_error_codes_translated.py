"""Every error code the server sends has a sentence in the frontend, in every language; otherwise the person sees
a bare code. Terminal end reasons likewise."""

from __future__ import annotations

import json
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
I18N = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"

#: Codes that never reach a person as a sentence: the frontend handles them itself.
HANDLED_ELSEWHERE = {"second_factor"}


def _codes(pattern: str) -> set[str]:
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        found |= set(re.findall(pattern, path.read_text(encoding="utf-8")))
    return found


def _languages() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(I18N.glob("*.json"))}


def test_the_scan_finds_codes() -> None:
    # Without this floor a broken pattern would find nothing and pass.
    assert len(_codes(r'fehler\(\s*"([a-z0-9_]+)"')) > 40
    assert len(_details()) > 10


def _details() -> set[str]:
    text = (APP / "services" / "ssh.py").read_text(encoding="utf-8")
    return set(re.findall(r'^DETAIL_[A-Z_]+ = "([a-z_]+)"', text, flags=re.MULTILINE))


def test_every_error_code_has_a_sentence() -> None:
    codes = _codes(r'fehler\(\s*"([a-z0-9_]+)"') | _codes(r'AccountError\(\s*"([a-z0-9_]+)"')
    codes -= HANDLED_ELSEWHERE
    for language, texts in _languages().items():
        missing = sorted(code for code in codes if code not in texts["errors"]["byCode"])
        assert not missing, f"{language}.json lacks errors.byCode for {missing}"


def test_every_terminal_end_has_a_sentence() -> None:
    details = _details() | {"session_gone", "not_signed_in"}
    for language, texts in _languages().items():
        missing = sorted(detail for detail in details if detail not in texts["terminal"]["fail"])
        assert not missing, f"{language}.json lacks terminal.fail for {missing}"
