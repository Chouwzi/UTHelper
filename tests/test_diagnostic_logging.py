from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from diagnostics.logging_setup import configure_logging


@pytest.fixture
def isolated_root_logger():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)

    try:
        yield root
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if handler not in original_handlers:
                handler.close()
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)


def _flush_root_handlers(root: logging.Logger) -> None:
    for handler in root.handlers:
        handler.flush()


def _read_log_bytes(data_dir: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in sorted((data_dir / "logs").glob("app.log*"))
        if path.is_file()
    )


def test_logging_is_idempotent_bounded_and_redacted(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    first = configure_logging(tmp_path, debug=True)
    second = configure_logging(tmp_path, debug=True)

    logging.getLogger("test").error(
        "token=%s student@ut.edu.vn",
        "diagnostic-message-secret",
    )
    _flush_root_handlers(isolated_root_logger)

    payload = _read_log_bytes(tmp_path)
    assert b"diagnostic-message-secret" not in payload
    assert b"student@ut.edu.vn" not in payload
    assert payload.count(b"[redacted]") >= 1
    assert sum(
        isinstance(handler, RotatingFileHandler)
        for handler in isolated_root_logger.handlers
    ) == 1
    assert first is second


def test_rotation_keeps_at_most_three_bounded_backups(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    runtime = configure_logging(tmp_path, debug=True)
    logger = logging.getLogger("rotation-test")
    handler = next(
        handler
        for handler in isolated_root_logger.handlers
        if isinstance(handler, RotatingFileHandler)
    )
    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 3

    # Exercise the real rollover implementation at a test-sized boundary. The
    # assertions above lock the production limits independently.
    handler.maxBytes = 16 * 1024

    for index in range(100):
        logger.error("record-%04d %s", index, "x" * 900)
    _flush_root_handlers(isolated_root_logger)

    files = sorted((tmp_path / "logs").glob("app.log*"))
    assert 2 <= len(files) <= 4
    assert all(path.stat().st_size <= 16 * 1024 for path in files)
    assert sum(path.stat().st_size for path in files) <= 4 * 16 * 1024
    runtime.close()


def test_final_exception_and_stack_formatting_is_redacted_before_write(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    configure_logging(tmp_path, debug=True)
    logger = logging.getLogger("exception-test")

    try:
        raise RuntimeError(
            "password=diagnostic-exception-secret student-exc@ut.edu.vn"
        )
    except RuntimeError:
        logger.exception("startup failed for token=diagnostic-outer-secret")

    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "stack capture",
        (),
        None,
    )
    record.stack_info = (
        "authorization: Bearer diagnostic-stack-secret "
        "stack-user@ut.edu.vn"
    )
    isolated_root_logger.handle(record)
    _flush_root_handlers(isolated_root_logger)

    payload = _read_log_bytes(tmp_path)
    for forbidden in (
        b"diagnostic-exception-secret",
        b"student-exc@ut.edu.vn",
        b"diagnostic-outer-secret",
        b"diagnostic-stack-secret",
        b"stack-user@ut.edu.vn",
    ):
        assert forbidden not in payload
    assert b"Traceback" in payload
    assert payload.count(b"[redacted]") >= 3


def test_unprintable_exception_cannot_break_file_logging(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    class UnprintableError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("password=diagnostic-format-secret")

    configure_logging(tmp_path, debug=True)
    try:
        raise UnprintableError()
    except UnprintableError:
        logging.getLogger("exception-test").exception("caught safely")
    _flush_root_handlers(isolated_root_logger)

    payload = _read_log_bytes(tmp_path)
    assert b"caught safely" in payload
    assert b"diagnostic-format-secret" not in payload


def test_close_is_idempotent_and_removes_only_the_owned_handler(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    external_stream = io.StringIO()
    external_handler = logging.StreamHandler(external_stream)
    isolated_root_logger.addHandler(external_handler)
    handlers_before = tuple(isolated_root_logger.handlers)
    runtime = configure_logging(tmp_path, debug=False)

    runtime.close()
    runtime.close()
    logging.getLogger("after-close").warning("external still works")

    assert tuple(isolated_root_logger.handlers) == handlers_before
    assert external_stream.getvalue().endswith("external still works\n")
    assert external_handler.stream is external_stream


def test_legacy_oversized_logs_are_safely_removed_before_handler_opens(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    oversized = b"x" * (2 * 1024 * 1024 + 1)
    (log_dir / "app.log").write_bytes(oversized)
    (log_dir / "app.log.1").write_bytes(oversized)
    (tmp_path / "debug_app.log").write_bytes(oversized)
    unrelated_paths = (
        log_dir / "debug_app.log",
        log_dir / "app.log.4",
        log_dir / "app.log.private",
    )
    for path in unrelated_paths:
        path.write_bytes(oversized)

    runtime = configure_logging(tmp_path, debug=False)

    assert (log_dir / "app.log").stat().st_size < 2 * 1024 * 1024
    assert not (log_dir / "app.log.1").exists()
    assert not (tmp_path / "debug_app.log").exists()
    assert all(path.read_bytes() == oversized for path in unrelated_paths)
    runtime.close()


@pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="platform has no symlink support",
)
def test_log_path_symlink_is_unlinked_without_touching_its_target(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    target = tmp_path / "outside.log"
    target.write_text("must remain", encoding="utf-8")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    link = log_dir / "app.log"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    runtime = configure_logging(tmp_path, debug=False)

    assert target.read_text(encoding="utf-8") == "must remain"
    assert not link.is_symlink()
    assert link.is_file()
    runtime.close()


@pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="platform has no symlink support",
)
def test_root_legacy_debug_symlink_is_unlinked_without_touching_its_target(
    tmp_path: Path,
    isolated_root_logger: logging.Logger,
) -> None:
    target = tmp_path / "outside-debug.log"
    target.write_text("must remain", encoding="utf-8")
    link = tmp_path / "debug_app.log"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    runtime = configure_logging(tmp_path, debug=False)

    assert target.read_text(encoding="utf-8") == "must remain"
    assert not link.exists()
    assert not link.is_symlink()
    runtime.close()


def test_main_import_owns_one_rotating_log_without_legacy_debug_file(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["APPDATA"] = str(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import logging; import main; "
                "from logging.handlers import RotatingFileHandler; "
                "print(sum(isinstance(h, RotatingFileHandler) "
                "for h in logging.getLogger().handlers))"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("1")
    assert (tmp_path / "UTHelper" / "logs" / "app.log").is_file()
    assert not (tmp_path / "UTHelper" / "debug_app.log").exists()
