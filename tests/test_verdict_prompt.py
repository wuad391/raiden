"""`_wait_for_verdict` semantics — keyboard-only verdict.

Under the simplified design the verdict prompt is keyboard only:
  - Enter / \\r / \\n → "success"
  - 'f' / 'F'        → "failure"
  - any other char   → None
  - 30 s timeout     → None

No interface/pedal/leader-arm verdict — the function takes no arguments.
"""

from __future__ import annotations

import inspect

import pytest

import raiden.recorder as rec_mod
from raiden.recorder import _wait_for_verdict


# ---------------------------------------------------------------------------
# Fake stdin / no-op terminal calls
# ---------------------------------------------------------------------------


class _FakeStdin:
    """Yields one character per .read(1); drains the queue as it goes."""

    def __init__(self, chars: list[str]) -> None:
        self._queue = list(chars)

    def fileno(self) -> int:
        return 0

    def read(self, n: int) -> str:
        return self._queue.pop(0) if self._queue else ""


def _patch_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec_mod.termios, "tcgetattr", lambda _fd: None)
    monkeypatch.setattr(rec_mod.termios, "tcsetattr", lambda *a, **kw: None)
    monkeypatch.setattr(rec_mod.tty, "setcbreak", lambda _fd: None)


def _install_keyboard(monkeypatch: pytest.MonkeyPatch, chars: list[str]) -> None:
    fake_stdin = _FakeStdin(chars)
    monkeypatch.setattr(rec_mod.sys, "stdin", fake_stdin)
    monkeypatch.setattr(
        rec_mod.select,
        "select",
        lambda r, w, x, t: ([fake_stdin], [], [])
        if fake_stdin._queue
        else ([], [], []),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_verdict_returns_success_on_enter(monkeypatch):
    _patch_terminal(monkeypatch)
    _install_keyboard(monkeypatch, ["\n"])
    assert _wait_for_verdict() == "success"


def test_verdict_returns_success_on_carriage_return(monkeypatch):
    _patch_terminal(monkeypatch)
    _install_keyboard(monkeypatch, ["\r"])
    assert _wait_for_verdict() == "success"


def test_verdict_returns_failure_on_f_key(monkeypatch):
    _patch_terminal(monkeypatch)
    _install_keyboard(monkeypatch, ["f"])
    assert _wait_for_verdict() == "failure"


def test_verdict_returns_failure_on_capital_f(monkeypatch):
    _patch_terminal(monkeypatch)
    _install_keyboard(monkeypatch, ["F"])
    assert _wait_for_verdict() == "failure"


def test_verdict_returns_none_on_other_key(monkeypatch):
    _patch_terminal(monkeypatch)
    _install_keyboard(monkeypatch, ["x"])
    assert _wait_for_verdict() is None


def test_wait_for_verdict_takes_no_arguments():
    """Pin the simplify-pass: keyboard-only verdict needs no inputs."""
    params = inspect.signature(_wait_for_verdict).parameters
    assert params == {}
