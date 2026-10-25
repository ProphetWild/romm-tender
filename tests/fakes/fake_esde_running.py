"""In-memory ``EsdeRunningFn`` for tests."""

from __future__ import annotations


class FakeEsdeRunning:
    """Answers ``running``, which a test sets to say whether ES-DE runs; ``calls`` counts the questions."""

    def __init__(self, *, running: bool = False) -> None:
        self.running = running
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.running
