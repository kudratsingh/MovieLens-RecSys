"""Give a serving module's logger somewhere to write when its runner did not.

uvicorn configures ``uvicorn``, ``uvicorn.error`` and ``uvicorn.access`` and
leaves every other logger exactly as it found it — no handler anywhere on the
chain, and an unconfigured root logger sitting at WARNING. The API process never
noticed, because ``src/serving/app.py`` calls ``logging.basicConfig`` at import
and the root handler that installs catches everything beneath it. The model
sidecar has no such call, so every ``logger.info`` in it was formatted by nobody
and dropped, including the two lines that say what the process is serving: the
retrieval family and index type a SASRec bundle was realised into, and the warm
line naming the family, candidate, ranker and feature versions. ``docker logs``
on the sidecar showed a torch ``UserWarning`` and nothing else, which is not
enough to answer "which retriever did this worker load" during a champion swap
— the moment when it is the only question worth asking.

The fix is the smallest one that answers it: one handler on one named logger, at
INFO, and only when nothing upstream is already handling. No ``basicConfig``, no
handler on the root logger, no touching uvicorn's own loggers, and no global
level change — a process that has configured logging keeps exactly the
configuration it chose, because ``hasHandlers()`` is true there and this returns
having done nothing.

Dependency-free, in the same spirit as ``src.serving.policy``: the modules that
call it are imported in images with deliberately different dependency sets, and
a logging helper must never be the reason one of them cannot import.
"""

from __future__ import annotations

import logging
import sys

# The format `src/serving/app.py` and every `src/**` entry point already use, so
# a line from the sidecar reads the same as a line from the API.
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def ensure_stdout_logging(logger: logging.Logger, *, level: int = logging.INFO) -> bool:
    """Attach a stdout handler to ``logger`` when nothing on its chain has one.

    Returns whether a handler was attached, so a caller — or a test — can say so
    without reaching into ``logging`` internals.

    stdout rather than stderr: the line this exists to deliver is an ordinary
    boot fact rather than a diagnostic, and putting it on the same stream as the
    rest of the container's startup output is what makes one ``docker logs`` read
    in order.
    """
    if logger.hasHandlers():
        return False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    # Set on this logger alone, and only when it has no level of its own. Without
    # it the record is filtered before it ever reaches the handler: a logger with
    # no level inherits the root's, and an unconfigured root logger is at
    # WARNING, so every INFO line would still be dropped with a handler attached.
    if logger.level == logging.NOTSET:
        logger.setLevel(level)
    return True
