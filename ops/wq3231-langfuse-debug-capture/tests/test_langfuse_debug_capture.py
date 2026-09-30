import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import langfuse_debug_capture as ldc  # noqa: E402


def _reset_langfuse_logger():
    logger = logging.getLogger("langfuse")
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    return logger


def test_without_install_debug_is_suppressed_by_root_level():
    """Reproduces the prod bug: litellm's proxy sets the root logger to
    WARNING and never gives 'langfuse' its own level, so log.debug(exception)
    in langfuse/parse_error.py:handle_exception is silently dropped."""
    logging.getLogger().setLevel(logging.WARNING)
    logger = _reset_langfuse_logger()

    assert logger.getEffectiveLevel() == logging.WARNING
    assert not logger.isEnabledFor(logging.DEBUG)


def test_install_sets_debug_level_independent_of_root(tmp_path):
    logging.getLogger().setLevel(logging.WARNING)
    logger = _reset_langfuse_logger()

    log_path = str(tmp_path / "langfuse-debug.log")
    ldc.install(log_path)

    assert logger.level == logging.DEBUG
    assert logger.isEnabledFor(logging.DEBUG)

    logger.debug("boom: ConnectError('all connection attempts failed')")
    for h in logger.handlers:
        h.flush()

    with open(log_path) as f:
        content = f.read()
    assert "boom: ConnectError" in content


def test_install_is_idempotent(tmp_path):
    _reset_langfuse_logger()
    log_path = str(tmp_path / "langfuse-debug.log")

    h1 = ldc.install(log_path)
    h2 = ldc.install(log_path)

    logger = logging.getLogger("langfuse")
    assert h1 is h2
    assert len(logger.handlers) == 1


def test_install_defaults_path_from_env(tmp_path, monkeypatch):
    _reset_langfuse_logger()
    log_path = str(tmp_path / "env-configured.log")
    monkeypatch.setenv(ldc.ENV_VAR, log_path)

    ldc.install()

    assert os.path.exists(log_path)
