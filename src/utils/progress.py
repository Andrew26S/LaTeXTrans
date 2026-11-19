"""Route workflow messages and stage progress to the current run's output."""

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Protocol


class Reporter(Protocol):
    def log(self, message: str) -> None: ...
    def progress(self, percent: int, message: str) -> None: ...


class CliReporter:
    def __init__(self):
        self.line_width = 0

    def log(self, message: str) -> None:
        if self.line_width:
            print()
            self.line_width = 0
        print(message)

    def progress(self, percent: int, message: str) -> None:
        line = f"[{percent:3d}%] {message}"
        if not sys.stdout.isatty():
            self.log(line)
            return
        print("\r" + line.ljust(self.line_width), end="", flush=True)
        self.line_width = len(line)
        if percent == 100:
            print()
            self.line_width = 0


_reporter: ContextVar[Optional[Reporter]] = ContextVar("latextrans_reporter", default=None)


@contextmanager
def use_reporter(reporter: Reporter):
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def _current_reporter() -> Reporter:
    reporter = _reporter.get()
    if reporter is None:
        reporter = CliReporter()
        _reporter.set(reporter)
    return reporter


def log_message(message: str) -> None:
    _current_reporter().log(str(message))


def report_progress(percent: int, message: str) -> None:
    _current_reporter().progress(max(0, min(100, percent)), message)
