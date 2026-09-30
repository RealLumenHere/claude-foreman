"""Attach a dedicated DEBUG handler to the 'langfuse' logger (wq#3231).

langfuse-python's IngestionConsumer.upload() catches every exception raised
by _upload_batch() and logs only the generic defaultErrorResponse string
("Unexpected error occurred...") at ERROR; the real exception is logged via
log.debug(exception) first (langfuse/parse_error.py:98-102 in the pinned
2.57.13a0 wheel), which is a no-op whenever the effective level of the
'langfuse' logger is above DEBUG. litellm's proxy sets the root logger to
its configured log_level (WARNING in prod, per
ops/wq3122-litellm-langfuse-trace-drop/README.md) and never gives 'langfuse'
an explicit level of its own, so it inherits WARNING and every log.debug(exception)
call is silently dropped before formatting.

install() sets the 'langfuse' logger's own level to DEBUG. Python's logging
module resolves whether to build a record from the level of the logger a
call originates on, not an ancestor's level, so this unblocks
log.debug(exception) regardless of the root/proxy log_level. A dedicated
FileHandler then captures only that logger's records, so the real exception
survives without switching the whole proxy to verbose DEBUG output.
"""

import logging
import os
from typing import Optional

DEFAULT_LOG_PATH = "/var/log/litellm/langfuse-debug.log"
ENV_VAR = "LANGFUSE_DEBUG_CAPTURE_PATH"


def install(log_path: Optional[str] = None) -> logging.Handler:
    """Idempotently attach the capture handler to the 'langfuse' logger.

    Safe to call more than once (e.g. re-imported across worker forks):
    returns the existing handler instead of attaching a duplicate.
    """
    log_path = log_path or os.environ.get(ENV_VAR, DEFAULT_LOG_PATH)
    logger = logging.getLogger("langfuse")
    logger.setLevel(logging.DEBUG)

    for existing in logger.handlers:
        if getattr(existing, "_wq3231_capture", False):
            return existing

    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler._wq3231_capture = True
    logger.addHandler(handler)
    return handler
