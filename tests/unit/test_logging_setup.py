"""The sidecar's boot lines have to reach stdout, without hijacking anything.

Two properties, and the second is the one that keeps this safe to import from a
serving module. A logger nothing upstream handles gets one stdout handler at
INFO, so ``sasrec_retriever_loaded …`` and ``Model server warm …`` actually
appear in ``docker logs`` under a uvicorn that configures only its own loggers.
A logger that *is* already handled — the API process, which calls
``basicConfig`` at import, or a pytest run — is left exactly as it was, so
nothing here can double-print a line or override a configuration somebody chose.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from src.serving.logging_setup import ensure_stdout_logging


@contextmanager
def _unconfigured_root(name: str) -> Iterator[logging.Logger]:
    """A named logger in a process whose root has no handlers, as uvicorn leaves it.

    Deliberately a context manager entered inside the test body rather than a
    fixture: pytest's logging plugin installs a fresh root handler at the start
    of each test phase, so a fixture that cleared them during setup would find
    one back again by the time the test ran — and every assertion here is about
    what happens when there is genuinely nothing upstream.
    """
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    try:
        yield logger
    finally:
        root.handlers[:] = saved
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)


def test_an_unhandled_logger_gets_one_stdout_handler_at_info() -> None:
    with _unconfigured_root("test.logging_setup.unhandled") as logger:
        assert ensure_stdout_logging(logger) is True

        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stdout
        # Without this the record never reaches the handler at all: an
        # unconfigured root logger sits at WARNING and an INFO line inherits it.
        assert logger.isEnabledFor(logging.INFO)


def test_the_boot_line_actually_reaches_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    with _unconfigured_root("test.logging_setup.emits") as logger:
        ensure_stdout_logging(logger)
        logger.info("sasrec_retriever_loaded family=%s index_type=%s", "sasrec", "flat-ip-exact")

    captured = capsys.readouterr()
    assert "sasrec_retriever_loaded family=sasrec index_type=flat-ip-exact" in captured.out


def test_a_logger_something_upstream_already_handles_is_left_alone() -> None:
    """The API process configures logging itself; this must not touch it."""
    with _unconfigured_root("test.logging_setup.already_handled") as logger:
        logging.getLogger().addHandler(logging.NullHandler())

        assert ensure_stdout_logging(logger) is False
        assert logger.handlers == []


def test_a_logger_with_its_own_level_keeps_it() -> None:
    with _unconfigured_root("test.logging_setup.own_level") as logger:
        logger.setLevel(logging.WARNING)

        ensure_stdout_logging(logger)

        assert logger.level == logging.WARNING
