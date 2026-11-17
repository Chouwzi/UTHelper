"""Tests for path-safe archive inspection and single-entry extraction."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import tarfile
import tempfile
import threading
import warnings
import zipfile
from pathlib import Path
from typing import Optional

import pytest

import core.courses.archive_service as archive_mod
from core.courses.archive_service import (
    ArchiveSecurityError,
    ArchiveService,
    find_rar_cli,
    find_windows_rar_cli,
    sanitize_archive_member_path,
)


def _make_sample_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Chuong1/Bai1.txt", "Xin chao UTH")
        zf.writestr("Chuong1/Sub/sheet.xlsx", b"PK-stub")
        zf.writestr("README.md", "# Muc luc")
    return buf.getvalue()


def _make_sample_tar(mode: str = "w:gz") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        root_dir = tarfile.TarInfo(".")
        root_dir.type = tarfile.DIRTYPE
        tf.addfile(root_dir)

        sub_dir = tarfile.TarInfo("./Chuong1")
        sub_dir.type = tarfile.DIRTYPE
        tf.addfile(sub_dir)

        sym = tarfile.TarInfo("Chuong1/evil_symlink")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/passwd"
        tf.addfile(sym)

        hard = tarfile.TarInfo("Chuong1/evil_hardlink")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "/etc/shadow"
        tf.addfile(hard)

        payload = b"Noi dung bai giang TAR"
        file_info = tarfile.TarInfo("./Chuong1/Bai2.txt")
        file_info.size = len(payload)
        tf.addfile(file_info, io.BytesIO(payload))

        readme_bytes = b"# Readme"
        readme_info = tarfile.TarInfo("README.md")
        readme_info.size = len(readme_bytes)
        tf.addfile(readme_info, io.BytesIO(readme_bytes))
    return buf.getvalue()


class _CountingStream:
    """Bounded test stream that tracks total bytes read to prove capped streaming."""

    def __init__(self, total_size: int, chunk_byte: bytes = b"A") -> None:
        self._remaining = total_size
        self._chunk_byte = chunk_byte
        self.bytes_read = 0
        self.closed = False

    def __enter__(self) -> "_CountingStream":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def read(self, size: int = -1) -> bytes:
        if self.closed or self._remaining <= 0:
            return b""
        to_emit = self._remaining if size is None or size < 0 else min(size, self._remaining)
        self._remaining -= to_emit
        self.bytes_read += to_emit
        return self._chunk_byte * to_emit

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    """Minimal Popen test double supporting incremental byte streaming, kill, and stderr."""

    def __init__(
        self,
        *,
        stdout_data: bytes = b"",
        stderr_data: bytes = b"",
        returncode: int = 0,
        stdout_stream: Optional[object] = None,
        stderr_stream: Optional[object] = None,
        block_until_killed: bool = False,
    ) -> None:
        self.stdout = stdout_stream if stdout_stream is not None else io.BytesIO(stdout_data)
        self.stderr = stderr_stream if stderr_stream is not None else io.BytesIO(stderr_data)
        self._target_rc = returncode
        self.returncode: Optional[int] = None
        self.killed = False
        self.terminated = False
        self._block_until_killed = block_until_killed
        self._done = threading.Event()
        if not block_until_killed:
            self._done.set()

    def poll(self) -> Optional[int]:
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        self._done.wait(timeout=timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed else self._target_rc
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        if hasattr(self.stdout, "close"):
            try:
                self.stdout.close()
            except OSError:
                pass
        if hasattr(self.stderr, "close"):
            try:
                self.stderr.close()
            except OSError:
                pass
        self._done.set()

    def terminate(self) -> None:
        self.terminated = True
        self.kill()


class _EndlessRarListingStream:
    """Infinite 7z-style listing stream that tracks emitted chunks to prove early termination."""

    def __init__(self, chunk_size: int = 4096) -> None:
        self._index = 0
        self._buf = b"----------\n"
        self.bytes_read = 0
        self.closed = False
        self._chunk_size = chunk_size

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        target = self._chunk_size if size is None or size < 0 else min(size, self._chunk_size)
        while len(self._buf) < target:
            entry = (
                f"Path = dir/file_{self._index}.txt\n"
                "Folder = -\n"
                "Size = 10\n"
                "Encrypted = -\n\n"
            ).encode("utf-8")
            self._buf += entry
            self._index += 1
        out = self._buf[:target]
        self._buf = self._buf[target:]
        self.bytes_read += len(out)
        return out

    def close(self) -> None:
        self.closed = True


def test_sanitize_archive_member_path_rejects_traversal():
    assert sanitize_archive_member_path("Chuong1\\Bai1.txt") == "Chuong1/Bai1.txt"
    for bad in ("../evil.txt", "/etc/passwd", "C:\\Windows\\win.ini", "a/../../b", ""):
        with pytest.raises(ArchiveSecurityError):
            sanitize_archive_member_path(bad)


def test_sanitize_archive_member_path_preserves_dotfiles_and_rejects_unsafe_forms():
    assert sanitize_archive_member_path(".gitignore") == ".gitignore"
    assert sanitize_archive_member_path("./.gitignore") == ".gitignore"
    assert sanitize_archive_member_path(".env") == ".env"
    assert sanitize_archive_member_path("dir/.hidden") == "dir/.hidden"
    assert sanitize_archive_member_path("./dir/./.env") == "dir/.env"
    assert sanitize_archive_member_path("Chuong1/") == "Chuong1"
    # Outer whitespace is not silently stripped to alias a clean filename
    assert sanitize_archive_member_path(" lead.txt") == " lead.txt"
    assert sanitize_archive_member_path("trail.txt ") == "trail.txt "

    unsafe_paths = (
        "a/../b",
        "../x",
        "a\\..\\..\\b",
        "dir/..",
        "a/.. ./b",
        "a/ . /b",
        "a/   /b",
        "a/.../b",
        ".. ",
        " ..",
        " /evil.txt",
        "a\x00b",
        "a\nb",
        "a\rb",
        "a\tb",
        "a\x7fb",
        "//host/share",
        "\\\\host\\share",
        "C:foo",
        "D:/bar",
        "ads.txt:Zone.Identifier",
        "   ",
        ".",
        "./",
    )
    for bad in unsafe_paths:
        with pytest.raises(ArchiveSecurityError):
            sanitize_archive_member_path(bad)


def test_archive_service_builds_tree_and_extracts_single_zip_entry():
    svc = ArchiveService(max_entries=100, max_uncompressed_bytes=1024 * 1024)
    raw = _make_sample_zip()
    tree = svc.inspect_bytes("slides.zip", raw)
    assert tree.supported is True
    assert tree.format_name == "zip"
    assert tree.total_files == 3
    root_names = [n.name for n in tree.root.children]
    assert root_names == ["Chuong1", "README.md"]  # dirs first, alphabetical

    chuong1 = tree.root.children[0]
    assert chuong1.is_dir is True
    assert [c.name for c in chuong1.children] == ["Sub", "Bai1.txt"]

    data = svc.extract_entry_bytes("slides.zip", raw, "Chuong1/Bai1.txt")
    assert data == b"Xin chao UTH"


def test_archive_service_rar_fallback_when_cli_unavailable():
    svc = ArchiveService(rar_tool_finder=lambda: None)
    res = svc.inspect_bytes("baitap.rar", b"Rar!\x1a\x07\x00dummy")
    assert res.supported is False
    assert res.format_name == "rar"
    assert "ZArchiver" in res.unsupported_reason or "WinRAR" in res.unsupported_reason


@pytest.mark.parametrize(
    ("file_name", "tar_mode"),
    [
        ("slides.tar.gz", "w:gz"),
        ("slides.tgz", "w:gz"),
        ("slides.tar.bz2", "w:bz2"),
        ("slides.tbz2", "w:bz2"),
        ("slides.tar.xz", "w:xz"),
        ("slides.txz", "w:xz"),
        ("slides.tar", "w"),
    ],
)
def test_archive_service_tar_builds_tree_skips_dot_and_links_and_extracts(
    file_name: str, tar_mode: str
):
    svc = ArchiveService()
    raw = _make_sample_tar(mode=tar_mode)
    tree = svc.inspect_bytes(file_name, raw)

    assert tree.supported is True
    assert tree.format_name == "tar"
    assert tree.total_files == 2
    assert [n.name for n in tree.root.children] == ["Chuong1", "README.md"]
    chuong1 = tree.root.children[0]
    assert [c.name for c in chuong1.children] == ["Bai2.txt"]

    assert svc.extract_entry_bytes(file_name, raw, "Chuong1/Bai2.txt") == b"Noi dung bai giang TAR"
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes(file_name, raw, "Chuong1/evil_symlink")
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes(file_name, raw, "Chuong1/evil_hardlink")


def test_archive_service_tar_rejects_tarslip():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"evil"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    svc = ArchiveService()
    with pytest.raises(ArchiveSecurityError):
        svc.inspect_bytes("evil.tar.gz", buf.getvalue())


def test_archive_service_tar_uses_incremental_next_and_bounded_decompression(monkeypatch):
    monkeypatch.setattr(
        tarfile.TarFile,
        "getmembers",
        lambda self: pytest.fail("getmembers() must not be called; use tf.next()"),
    )

    # Build a small compressed .tar.gz / .tar.bz2 bomb (4 MiB zeros compressed to a few KB)
    bomb_payload = b"\x00" * (4 * 1024 * 1024)
    for file_name, mode in (("bomb.tar.gz", "w:gz"), ("bomb.tar.bz2", "w:bz2")):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=mode) as tf:
            decoy = tarfile.TarInfo("decoy.bin")
            decoy.size = len(bomb_payload)
            tf.addfile(decoy, io.BytesIO(bomb_payload))
            target = tarfile.TarInfo("target.txt")
            target.size = 2
            tf.addfile(target, io.BytesIO(b"ok"))

        raw_bomb = buf.getvalue()
        assert len(raw_bomb) < 64 * 1024
        svc = ArchiveService(max_uncompressed_bytes=64 * 1024, max_single_entry_bytes=32 * 1024)
        with pytest.raises(ArchiveSecurityError):
            svc.inspect_bytes(file_name, raw_bomb)
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes(file_name, raw_bomb, "target.txt")

    # Even when a member file claims size=2, a huge compressed PAX extended header
    # inside .tar.gz must be halted by _ByteLimitStream inside tf.next()!
    pax_payload = b"20 path=tiny.txt\n" + (b"1000000 comment=" + (b"A" * 999980) + b"\n")
    pax_buf = io.BytesIO()
    with tarfile.open(fileobj=pax_buf, mode="w:gz") as tf:
        pax_info = tarfile.TarInfo("PaxHeaders.0/tiny.txt")
        pax_info.type = tarfile.XHDTYPE
        pax_info.size = len(pax_payload)
        tf.addfile(pax_info, io.BytesIO(pax_payload))
        tiny = tarfile.TarInfo("tiny.txt")
        tiny.size = 2
        tf.addfile(tiny, io.BytesIO(b"hi"))
    gz_pax_bomb = pax_buf.getvalue()
    assert len(gz_pax_bomb) < 16 * 1024
    svc_small = ArchiveService(max_entries=10, max_uncompressed_bytes=1024)
    with pytest.raises(ArchiveSecurityError):
        svc_small.inspect_bytes("pax_bomb.tar.gz", gz_pax_bomb)


def test_archive_service_corrupt_archives_return_unsupported_cleanly():
    svc = ArchiveService()
    for name, expected_fmt in (
        ("broken.zip", "zip"),
        ("broken.tar", "tar"),
        ("broken.tar.gz", "tar"),
        ("broken.tgz", "tar"),
        ("broken.tar.bz2", "tar"),
        ("broken.tbz2", "tar"),
        ("broken.tar.xz", "tar"),
        ("broken.txz", "tar"),
    ):
        res = svc.inspect_bytes(name, b"not-a-valid-archive-payload")
        assert res.supported is False
        assert res.format_name == expected_fmt
        assert res.unsupported_reason.strip() != ""


def test_archive_service_enforces_entry_total_and_spoofed_single_entry_caps(monkeypatch):
    raw_zip = _make_sample_zip()
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_entries=2).inspect_bytes("slides.zip", raw_zip)
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_uncompressed_bytes=5).inspect_bytes("slides.zip", raw_zip)

    raw_tar = _make_sample_tar()
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_entries=1).inspect_bytes("slides.tar.gz", raw_tar)
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_uncompressed_bytes=5).inspect_bytes("slides.tar.gz", raw_tar)

    # Real ZIP and TAR entries exceeding max_single_entry_bytes
    big_payload = b"X" * 1024
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", big_payload)
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        tinfo = tarfile.TarInfo("big.txt")
        tinfo.size = len(big_payload)
        tf.addfile(tinfo, io.BytesIO(big_payload))

    svc = ArchiveService(max_single_entry_bytes=512)
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("big.zip", zip_buf.getvalue(), "big.txt")
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("big.tar.gz", tar_buf.getvalue(), "big.txt")

    # Spoofed header claiming file_size=0 while stream actually yields 1 MiB:
    # verify _read_stream_capped stops at max_entry + 1 (513 bytes) and raises ArchiveSecurityError.
    zero_zip_buf = io.BytesIO()
    with zipfile.ZipFile(zero_zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("spoof.txt", b"")
    zip_stream = _CountingStream(1024 * 1024)
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, *a, **kw: zip_stream)
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("spoof.zip", zero_zip_buf.getvalue(), "spoof.txt")
    assert zip_stream.bytes_read == 513

    zero_tar_buf = io.BytesIO()
    with tarfile.open(fileobj=zero_tar_buf, mode="w:gz") as tf:
        tinfo = tarfile.TarInfo("spoof.txt")
        tinfo.size = 0
        tf.addfile(tinfo, io.BytesIO(b""))
    tar_stream = _CountingStream(1024 * 1024)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: tar_stream)
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("spoof.tar.gz", zero_tar_buf.getvalue(), "spoof.txt")
    assert tar_stream.bytes_read == 513


def test_archive_service_skips_and_rejects_zip_special_modes_and_unsafe_dirs():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notes.txt", b"ok")
        for idx, mode in enumerate(
            (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK)
        ):
            info = zipfile.ZipInfo(f"special_{idx}")
            info.external_attr = (mode | 0o777) << 16
            zf.writestr(info, b"ignored")

    raw = buf.getvalue()
    svc = ArchiveService()
    tree = svc.inspect_bytes("with_specials.zip", raw)
    assert tree.supported is True
    assert [c.name for c in tree.root.children] == ["notes.txt"]
    assert svc.extract_entry_bytes("with_specials.zip", raw, "notes.txt") == b"ok"
    for idx in range(5):
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("with_specials.zip", raw, f"special_{idx}")

    # Unsafe directory entry in ZIP must also raise ArchiveSecurityError during extraction
    bad_dir_buf = io.BytesIO()
    with zipfile.ZipFile(bad_dir_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil_dir/", b"")
        zf.writestr("ok.txt", b"safe")
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("bad_dir.zip", bad_dir_buf.getvalue(), "ok.txt")


def test_archive_service_detects_encrypted_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("secret.txt", b"cipher-bytes")
    raw = bytearray(buf.getvalue())
    # Patch general purpose bit flag (bit 0 = 0x01) in both local file header and central directory
    local_idx = raw.find(b"PK\x03\x04")
    cd_idx = raw.find(b"PK\x01\x02")
    raw[local_idx + 6] |= 0x01
    raw[cd_idx + 8] |= 0x01

    svc = ArchiveService()
    tree = svc.inspect_bytes("secret.zip", bytes(raw))
    assert tree.supported is False
    assert tree.format_name == "zip"
    assert "mật khẩu" in tree.unsupported_reason.lower()

    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("secret.zip", bytes(raw), "secret.txt")


def test_archive_service_rejects_file_dir_collisions_deduplicates_and_handles_deep_nesting():
    svc = ArchiveService()

    # 1. ZIP file-vs-dir collision (file "a" then "a/b.txt", and dir "a/" then file "a")
    for entries in (
        [("a", b"file"), ("a/b.txt", b"nested")],
        [("a/", b""), ("a", b"file")],
    ):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as zf:
            for name, payload in entries:
                zf.writestr(name, payload)
        with pytest.raises(ArchiveSecurityError):
            svc.inspect_bytes("collide.zip", zbuf.getvalue())
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("collide.zip", zbuf.getvalue(), "a")

    # 2. TAR file-vs-dir collision
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tf:
        f1 = tarfile.TarInfo("a")
        f1.size = 1
        tf.addfile(f1, io.BytesIO(b"x"))
        f2 = tarfile.TarInfo("a/b.txt")
        f2.size = 1
        tf.addfile(f2, io.BytesIO(b"y"))
    with pytest.raises(ArchiveSecurityError):
        svc.inspect_bytes("collide.tar", tbuf.getvalue())
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("collide.tar", tbuf.getvalue(), "a")

    # 3. Duplicate regular entries deduplicate cleanly and extract latest regular entry
    dup_zip = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(dup_zip, "w") as zf:
            zf.writestr("dup.txt", b"old")
            zf.writestr("dup.txt", b"latest-version")
    dup_tree = svc.inspect_bytes("dup.zip", dup_zip.getvalue())
    assert dup_tree.total_files == 1
    assert len(dup_tree.root.children) == 1
    assert dup_tree.root.children[0].size == len(b"latest-version")
    assert svc.extract_entry_bytes("dup.zip", dup_zip.getvalue(), "dup.txt") == b"latest-version"

    dup_tar = io.BytesIO()
    with tarfile.open(fileobj=dup_tar, mode="w") as tf:
        for content in (b"old", b"newer-tar"):
            ti = tarfile.TarInfo("dup.txt")
            ti.size = len(content)
            tf.addfile(ti, io.BytesIO(content))
    dup_tar_tree = svc.inspect_bytes("dup.tar", dup_tar.getvalue())
    assert dup_tar_tree.total_files == 1
    assert dup_tar_tree.root.children[0].size == len(b"newer-tar")
    assert svc.extract_entry_bytes("dup.tar", dup_tar.getvalue(), "dup.txt") == b"newer-tar"

    # 4. Link-shadowed duplicates must be rejected on extraction (both orders)
    for link_first in (True, False):
        shadow_zip = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(shadow_zip, "w") as zf:
                if link_first:
                    li = zipfile.ZipInfo("a.txt")
                    li.external_attr = (stat.S_IFLNK | 0o777) << 16
                    zf.writestr(li, b"/etc/passwd")
                    zf.writestr("a.txt", b"benign")
                else:
                    zf.writestr("a.txt", b"benign")
                    li = zipfile.ZipInfo("a.txt")
                    li.external_attr = (stat.S_IFLNK | 0o777) << 16
                    zf.writestr(li, b"/etc/passwd")
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("shadow.zip", shadow_zip.getvalue(), "a.txt")

        shadow_tar = io.BytesIO()
        with tarfile.open(fileobj=shadow_tar, mode="w") as tf:
            if link_first:
                sym = tarfile.TarInfo("a.txt")
                sym.type = tarfile.SYMTYPE
                sym.linkname = "/etc/passwd"
                tf.addfile(sym)
                reg = tarfile.TarInfo("a.txt")
                reg.size = 6
                tf.addfile(reg, io.BytesIO(b"benign"))
            else:
                reg = tarfile.TarInfo("a.txt")
                reg.size = 6
                tf.addfile(reg, io.BytesIO(b"benign"))
                sym = tarfile.TarInfo("a.txt")
                sym.type = tarfile.SYMTYPE
                sym.linkname = "/etc/passwd"
                tf.addfile(sym)
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("shadow.tar", shadow_tar.getvalue(), "a.txt")

    # 5. Deeply nested archive (1100+ segments) must succeed without RecursionError
    deep_path = "a/" * 1100 + "f.txt"
    deep_zip = io.BytesIO()
    with zipfile.ZipFile(deep_zip, "w") as zf:
        zf.writestr(deep_path, b"deep-ok")
    deep_tree = svc.inspect_bytes("deep.zip", deep_zip.getvalue())
    assert deep_tree.supported is True
    assert deep_tree.total_files == 1
    assert svc.extract_entry_bytes("deep.zip", deep_zip.getvalue(), deep_path) == b"deep-ok"


def test_find_rar_cli_alias_and_non_nt_guard(monkeypatch):
    assert find_rar_cli is find_windows_rar_cli
    monkeypatch.setattr(
        "core.courses.archive_service.shutil.which",
        lambda name: r"C:\Program Files\7-Zip\7z.exe" if name == "7z" else None,
    )
    assert find_windows_rar_cli() == r"C:\Program Files\7-Zip\7z.exe"

    # Non-NT platforms must not probe fixed C:\Program Files paths
    monkeypatch.setattr("core.courses.archive_service.shutil.which", lambda name: None)
    monkeypatch.setattr(archive_mod.os, "name", "posix")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    assert find_windows_rar_cli() is None


def test_archive_service_rar_7z_slt_parsing_unicode_links_and_safe_extraction(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "rar_tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TEMP", str(temp_dir))

    seen_calls: list[tuple[list[str], dict[str, object]]] = []
    slt_stdout = (
        "7-Zip 24.08 (x64)\n\n"
        "Listing archive: C:\\Temp\\tmp_test.rar\n\n"
        "--\n"
        "Path = C:\\Temp\\tmp_test.rar\n"
        "Type = Rar5\n"
        "Physical Size = 256\n"
        "Encrypted = -\n\n"
        "----------\n"
        "Path = Chương 1\n"
        "Folder = +\n"
        "Size = 0\n\n"
        "Path = Chương 1\\Sub\n"
        "Folder = +\n"
        "Size = 0\n\n"
        "Path = Chương 1\\Sub\\Bài tập.pdf\n"
        "Folder = -\n"
        "Size = 15\n"
        "Encrypted = -\n\n"
        "Path = link_sym.txt\n"
        "Folder = -\n"
        "Size = 0\n"
        "Symbolic Link = /etc/passwd\n\n"
        "Path = link_hard.txt\n"
        "Folder = -\n"
        "Size = 15\n"
        "Hard Link = Chương 1\\Sub\\Bài tập.pdf\n\n"
        "Path = attr_link.txt\n"
        "Folder = -\n"
        "Size = 0\n"
        "Attributes = A_L\n\n"
        "Path = unix_attr_link.txt\n"
        "Folder = -\n"
        "Size = 0\n"
        "Attributes = lrwxrwxrwx\n\n"
        "Path = win_sys_file.txt\n"
        "Folder = -\n"
        "Size = 12\n"
        "Attributes = SA\n"
        "Encrypted = -\n\n"
        "Path = win_comp_file.txt\n"
        "Folder = -\n"
        "Size = 10\n"
        "Attributes = C\n"
        "Encrypted = -\n\n"
        "Path = mode_link.txt\n"
        "Folder = -\n"
        "Size = 0\n"
        "Mode = lrwxrwxrwx\n\n"
        "Path = README.md\n"
        "Folder = -\n"
        "Size = 8\n"
        "Encrypted = -\n"
    ).encode("utf-8")

    def _fake_popen(cmd, **kwargs):
        seen_calls.append((list(cmd), kwargs))
        if cmd[1] == "l":
            return _FakePopen(stdout_data=slt_stdout)
        return _FakePopen(stdout_data=b"Xin chao tu RAR")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    svc = ArchiveService(rar_tool_finder=lambda: r"C:\Program Files\7-Zip\7z.exe")

    tree = svc.inspect_bytes("lecture.rar", b"Rar!-stub")
    assert tree.supported is True
    assert tree.format_name == "rar"
    assert tree.total_files == 4
    assert [n.name for n in tree.root.children] == [
        "Chương 1",
        "README.md",
        "win_comp_file.txt",
        "win_sys_file.txt",
    ]
    chuong1 = tree.root.children[0]
    assert chuong1.is_dir is True
    assert [c.name for c in chuong1.children] == ["Sub"]
    assert chuong1.children[0].children[0].name == "Bài tập.pdf"
    assert chuong1.children[0].children[0].size == 15

    list_cmd, list_kwargs = seen_calls[0]
    assert list_cmd[:6] == [
        r"C:\Program Files\7-Zip\7z.exe",
        "l",
        "-slt",
        "-sccUTF-8",
        "-p-",
        "--",
    ]
    assert list_kwargs.get("stdin") is subprocess.DEVNULL
    assert list_kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)

    extracted = svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "Chương 1/Sub/Bài tập.pdf")
    assert extracted == b"Xin chao tu RAR"

    # extract_entry_bytes must run listing FIRST, then extract using exact raw_path
    assert seen_calls[1][0][:6] == [
        r"C:\Program Files\7-Zip\7z.exe",
        "l",
        "-slt",
        "-sccUTF-8",
        "-p-",
        "--",
    ]
    ext_cmd, ext_kwargs = seen_calls[2]
    assert ext_cmd[:6] == [
        r"C:\Program Files\7-Zip\7z.exe",
        "x",
        "-so",
        "-sccUTF-8",
        "-p-",
        "--",
    ]
    assert ext_cmd[-1] == "Chương 1\\Sub\\Bài tập.pdf"
    assert ext_kwargs.get("stdin") is subprocess.DEVNULL
    assert ext_kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # All link types must be rejected on extraction
    for link_name in (
        "link_sym.txt",
        "link_hard.txt",
        "attr_link.txt",
        "unix_attr_link.txt",
        "mode_link.txt",
    ):
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", link_name)

    assert svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "win_sys_file.txt") == b"Xin chao tu RAR"
    assert svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "win_comp_file.txt") == b"Xin chao tu RAR"

    # Missing file and directory target raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "missing.txt")
    with pytest.raises(FileNotFoundError):
        svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "Chương 1")

    assert list(temp_dir.iterdir()) == []


def test_archive_service_rar_unrar_lt_p_ierr_parsing_and_guards(monkeypatch):
    seen_calls: list[list[str]] = []
    lt_stdout = (
        "\nUNRAR 7.20 x64 freeware      Copyright (c) 1993-2026 Alexander Roshal\n\n"
        "Archive: C:\\Temp\\test.rar\n"
        "Details: RAR 5\n\n"
        "        Name: Chương 1\\Bài tập.pdf\n"
        "        Type: File\n"
        "        Size: 19\n"
        "  Attributes: ..A....\n\n"
        "        Name: Chương 1\\Thư mục rỗng\n"
        "        Type: Directory\n"
        "  Attributes: ...D...\n\n"
        "        Name: hard.txt\n"
        "        Type: Hard link\n"
        "      Target: Chương 1\\Bài tập.pdf\n"
        "        Size: 19\n\n"
        "        Name: sym.txt\n"
        "        Type: Windows symbolic link\n"
        "      Target: \\??\\C:\\secret.txt\n"
        "        Size: 0\n\n"
        "        Name: README.md\n"
        "        Type: File\n"
        "        Size: 7\n"
    ).encode("utf-8")

    def _fake_popen(cmd, **kwargs):
        seen_calls.append(list(cmd))
        if cmd[1] == "lt":
            return _FakePopen(stdout_data=lt_stdout)
        return _FakePopen(stdout_data=b"unrar-content", stderr_data=b"")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    svc = ArchiveService(rar_tool_finder=lambda: r"C:\Program Files\WinRAR\UnRAR.exe")

    tree = svc.inspect_bytes("lecture.rar", b"Rar!-stub")
    assert tree.supported is True
    assert tree.total_files == 2
    assert [n.name for n in tree.root.children] == ["Chương 1", "README.md"]
    chuong1 = tree.root.children[0]
    assert chuong1.is_dir is True
    assert [c.name for c in chuong1.children] == ["Thư mục rỗng", "Bài tập.pdf"]
    assert chuong1.children[0].is_dir is True
    assert chuong1.children[1].size == 19

    data = svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "Chương 1/Bài tập.pdf")
    assert data == b"unrar-content"
    assert seen_calls[0][:5] == [r"C:\Program Files\WinRAR\UnRAR.exe", "lt", "-p-", "-scf", "--"]
    assert seen_calls[1][:5] == [r"C:\Program Files\WinRAR\UnRAR.exe", "lt", "-p-", "-scf", "--"]
    assert seen_calls[2][:8] == [
        r"C:\Program Files\WinRAR\UnRAR.exe",
        "p",
        "-ierr",
        "-idq",
        "-p-",
        "-scf",
        "-@",
        "--",
    ]
    assert seen_calls[2][-1] == "Chương 1\\Bài tập.pdf"

    for link_name in ("hard.txt", "sym.txt"):
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", link_name)

    for unsafe_member in ("-rf", "@secret.txt", "*.pdf", "dir/file?.txt"):
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", unsafe_member)


def test_archive_service_rar_unrar_encrypted_and_ierr_stderr(monkeypatch):
    # 1. UnRAR lt reports Flags: encrypted
    lt_enc = (
        "Archive: C:\\Temp\\enc.rar\n"
        "Details: RAR 5\n\n"
        "        Name: secret.txt\n"
        "        Type: File\n"
        "        Size: 10\n"
        "       Flags: encrypted \n"
    ).encode("utf-8")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: _FakePopen(stdout_data=lt_enc)
    )
    svc = ArchiveService(rar_tool_finder=lambda: r"C:\Program Files\WinRAR\UnRAR.exe")
    res = svc.inspect_bytes("enc.rar", b"Rar!-stub")
    assert res.supported is False
    assert res.format_name == "rar"
    assert "mật khẩu" in res.unsupported_reason.lower()
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("enc.rar", b"Rar!-stub", "secret.txt")

    # 2. UnRAR p returns password error on stderr via -ierr even if listing claimed unencrypted
    lt_plain = (
        "Archive: C:\\Temp\\enc.rar\n\n"
        "        Name: secret.txt\n"
        "        Type: File\n"
        "        Size: 10\n"
    ).encode("utf-8")

    def _fake_popen_ierr(cmd, **kwargs):
        if cmd[1] == "lt":
            return _FakePopen(stdout_data=lt_plain)
        return _FakePopen(
            stdout_data=b"",
            stderr_data=b"\r\nIncorrect password for secret.txt\r\n",
            returncode=11,
        )

    monkeypatch.setattr(subprocess, "Popen", _fake_popen_ierr)
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("enc.rar", b"Rar!-stub", "secret.txt")


def test_archive_service_rar_zipslip_and_failure_cleanup_including_unlink_oserror(
    monkeypatch, tmp_path
):
    temp_dir = tmp_path / "rar_err_tmp"
    temp_dir.mkdir()
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TEMP", str(temp_dir))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    # 1. ZipSlip in RAR listing must raise ArchiveSecurityError and clean temp file
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: _FakePopen(
            stdout_data=b"----------\nPath = ../evil.txt\nFolder = -\nSize = 4\n"
        ),
    )
    svc = ArchiveService(rar_tool_finder=lambda: "7z")
    with pytest.raises(ArchiveSecurityError):
        svc.inspect_bytes("evil.rar", b"Rar!-stub")
    assert list(temp_dir.iterdir()) == []

    # 2. PermissionError during temp unlink must NOT mask ArchiveSecurityError
    orig_unlink = Path.unlink

    def _raise_perm(self, missing_ok=False):
        if self.suffix == ".rar":
            raise PermissionError("AV lock")
        return orig_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _raise_perm)
    with pytest.raises(ArchiveSecurityError):
        svc.inspect_bytes("evil_av.rar", b"Rar!-stub")
    monkeypatch.setattr(Path, "unlink", orig_unlink)
    for leftover in temp_dir.iterdir():
        leftover.unlink()

    # 3. Real watchdog timer kills hanging Popen -> supported=False gracefully + cleanup
    monkeypatch.setattr(archive_mod, "_RAR_LIST_TIMEOUT_SECONDS", 0.05)
    hanging_procs: list[_FakePopen] = []

    def _make_hanging(cmd, **kwargs):
        p = _FakePopen(block_until_killed=True)
        hanging_procs.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", _make_hanging)
    res_timeout = svc.inspect_bytes("slow.rar", b"Rar!-stub")
    assert res_timeout.supported is False
    assert res_timeout.format_name == "rar"
    assert res_timeout.unsupported_reason.strip() != ""
    assert hanging_procs[0].killed is True
    assert list(temp_dir.iterdir()) == []

    # 4. Non-zero CLI failure -> supported=False gracefully + temp file cleanup
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: _FakePopen(
            stdout_data=b"", stderr_data=b"ERROR: Cannot open archive", returncode=2
        ),
    )
    res_fail = svc.inspect_bytes("broken.rar", b"Rar!-stub")
    assert res_fail.supported is False
    assert res_fail.format_name == "rar"
    assert list(temp_dir.iterdir()) == []

    # 5. Encrypted RAR (7z reports Encrypted = +) -> supported=False gracefully + cleanup
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: _FakePopen(
            stdout_data=b"----------\nPath = secret.txt\nFolder = -\nSize = 10\nEncrypted = +\n"
        ),
    )
    res_enc = svc.inspect_bytes("encrypted.rar", b"Rar!-stub")
    assert res_enc.supported is False
    assert res_enc.format_name == "rar"
    assert res_enc.unsupported_reason.strip() != ""
    assert list(temp_dir.iterdir()) == []


def test_archive_service_rar_streams_bounded_and_kills_oversized_cli(monkeypatch, tmp_path):
    temp_dir = tmp_path / "rar_cap_tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    # 1. Endless RAR listing stream stops as soon as max_entries is exceeded and kills proc
    endless = _EndlessRarListingStream()
    list_proc = _FakePopen(stdout_stream=endless)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: list_proc)

    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_entries=3, rar_tool_finder=lambda: "7z").inspect_bytes(
            "giant.rar", b"Rar!-stub"
        )
    assert list_proc.killed is True
    assert endless.bytes_read <= 16384
    assert list(temp_dir.iterdir()) == []

    # 2. UnRAR lt enforces max_entries & max_uncompressed_bytes (no silent truncation)
    lt_two = (
        "Archive: caps.rar\n\n"
        "        Name: big.txt\n"
        "        Type: File\n"
        "        Size: 2048\n\n"
        "        Name: small.txt\n"
        "        Type: File\n"
        "        Size: 10\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: _FakePopen(stdout_data=lt_two)
    )
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(
            max_entries=1, rar_tool_finder=lambda: r"C:\Program Files\WinRAR\UnRAR.exe"
        ).inspect_bytes("caps.rar", b"Rar!-stub")
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(
            max_uncompressed_bytes=1024,
            rar_tool_finder=lambda: r"C:\Program Files\WinRAR\UnRAR.exe",
        ).inspect_bytes("caps.rar", b"Rar!-stub")

    # 3. Extraction stdout exceeding max_single_entry_bytes kills proc at max_entry + 1
    #    even when header lied about size=10, and drains >64 KiB stderr without deadlock
    lt_spoof = (
        "Archive: spoof.rar\n\n"
        "        Name: spoof.txt\n"
        "        Type: File\n"
        "        Size: 10\n"
    ).encode("utf-8")
    huge_out = _CountingStream(1024 * 1024)
    huge_err = _CountingStream(256 * 1024, chunk_byte=b"E")
    extract_procs: list[_FakePopen] = []

    def _fake_extract_popen(cmd, **kwargs):
        if cmd[1] == "lt":
            return _FakePopen(stdout_data=lt_spoof)
        p = _FakePopen(stdout_stream=huge_out, stderr_stream=huge_err)
        extract_procs.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", _fake_extract_popen)
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(
            max_single_entry_bytes=512,
            rar_tool_finder=lambda: r"C:\Program Files\WinRAR\UnRAR.exe",
        ).extract_entry_bytes("spoof.rar", b"Rar!-stub", "spoof.txt")
    assert extract_procs[0].killed is True
    assert huge_out.bytes_read == 513
    assert list(temp_dir.iterdir()) == []


def test_archive_service_rar_rejects_collisions_link_shadows_and_raw_wildcards(monkeypatch):
    # Duplicate link shadowing & file-vs-dir collision & wildcard raw_path inside RAR listing
    slt_shadow = (
        "----------\n"
        "Path = a.txt\nFolder = -\nSize = 5\n\n"
        "Path = a.txt\nFolder = -\nSize = 0\nSymbolic Link = /etc/passwd\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: _FakePopen(stdout_data=slt_shadow)
    )
    svc = ArchiveService(rar_tool_finder=lambda: "7z")
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("shadow.rar", b"Rar!-stub", "a.txt")

    slt_collide = (
        "----------\n"
        "Path = a\nFolder = -\nSize = 5\n\n"
        "Path = a\\b.txt\nFolder = -\nSize = 5\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: _FakePopen(stdout_data=slt_collide)
    )
    with pytest.raises(ArchiveSecurityError):
        svc.inspect_bytes("collide.rar", b"Rar!-stub")
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("collide.rar", b"Rar!-stub", "a")

    slt_glob = (
        "----------\n"
        "Path = sub\\*.txt\nFolder = -\nSize = 5\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: _FakePopen(stdout_data=slt_glob)
    )
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("glob.rar", b"Rar!-stub", "sub/*.txt")


def test_archive_service_preserves_dotfiles_nested_archives_and_handles_missing_or_unknown():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("./.gitignore", b"*.pyc\n")
        zf.writestr(".env", b"APP_ENV=test")
        zf.writestr("sub/.hidden", b"hidden")
        zf.writestr("nested.tar.gz", b"gz-bytes")
    raw = buf.getvalue()

    svc = ArchiveService()
    tree = svc.inspect_bytes("dotfiles.zip", raw)
    assert tree.supported is True
    assert tree.total_files == 4
    names = [n.name for n in tree.root.children]
    assert names == ["sub", ".env", ".gitignore", "nested.tar.gz"]
    nested_node = next(n for n in tree.root.children if n.name == "nested.tar.gz")
    assert nested_node.preview_kind == "archive"
    assert svc.extract_entry_bytes("dotfiles.zip", raw, ".gitignore") == b"*.pyc\n"
    assert svc.extract_entry_bytes("dotfiles.zip", raw, ".env") == b"APP_ENV=test"
    assert svc.extract_entry_bytes("dotfiles.zip", raw, "sub/.hidden") == b"hidden"

    with pytest.raises(FileNotFoundError):
        svc.extract_entry_bytes("dotfiles.zip", raw, "missing.txt")

    unknown = svc.inspect_bytes("archive.ace", b"**ACE**")
    assert unknown.supported is False
    assert unknown.format_name == "unknown"
    with pytest.raises(ValueError):
        svc.extract_entry_bytes("archive.ace", b"ace", "a.txt")
    with pytest.raises(ValueError):
        ArchiveService(max_entries=-1)


@pytest.mark.skipif(
    os.name != "nt"
    or not (
        Path(r"C:\Program Files\WinRAR\UnRAR.exe").is_file()
        and Path(r"C:\Program Files\WinRAR\Rar.exe").is_file()
    ),
    reason="Real WinRAR CLI binaries not installed on this host",
)
def test_archive_service_real_unrar_cli_vietnamese_empty_dir_and_links(monkeypatch, tmp_path):
    rar_tmp = tmp_path / "rar_tmp"
    rar_tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(rar_tmp))

    rar_exe = r"C:\Program Files\WinRAR\Rar.exe"
    unrar_exe = r"C:\Program Files\WinRAR\UnRAR.exe"
    src_dir = tmp_path / "src"
    (src_dir / "Chương 1" / "Thư mục rỗng").mkdir(parents=True)
    target_file = src_dir / "Chương 1" / "Bài tập.txt"
    payload = "Nội dung tiếng Việt UTH\n".encode("utf-8")
    target_file.write_bytes(payload)
    os.link(target_file, src_dir / "hardlink.txt")

    arc_path = tmp_path / "real_test.rar"
    created = subprocess.run(
        [rar_exe, "a", "-r", "-ep1", "-oh", str(arc_path), str(src_dir) + "\\*"],
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0
    rar_bytes = arc_path.read_bytes()

    svc = ArchiveService(rar_tool_finder=lambda: unrar_exe)
    tree = svc.inspect_bytes("real_test.rar", rar_bytes)
    assert tree.supported is True
    assert tree.format_name == "rar"
    assert tree.total_files == 1
    chuong1 = tree.root.children[0]
    assert chuong1.name == "Chương 1"
    assert [c.name for c in chuong1.children] == ["Thư mục rỗng", "Bài tập.txt"]
    assert chuong1.children[0].is_dir is True

    assert svc.extract_entry_bytes("real_test.rar", rar_bytes, "Chương 1/Bài tập.txt") == payload
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("real_test.rar", rar_bytes, "hardlink.txt")
    with pytest.raises(FileNotFoundError):
        svc.extract_entry_bytes("real_test.rar", rar_bytes, "Chương 1/Thư mục rỗng")

    # Also test encrypted real RAR archives (file-only -p and header -hp)
    for idx, flag in enumerate(("-psecret", "-hpsecret")):
        enc_path = tmp_path / f"enc_{idx}.rar"
        enc_created = subprocess.run(
            [rar_exe, "a", flag, "-ep1", str(enc_path), str(target_file)],
            capture_output=True,
            check=False,
        )
        assert enc_created.returncode == 0
        enc_bytes = enc_path.read_bytes()
        enc_tree = svc.inspect_bytes(f"enc_{idx}.rar", enc_bytes)
        assert enc_tree.supported is False
        assert "mật khẩu" in enc_tree.unsupported_reason.lower()
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes(f"enc_{idx}.rar", enc_bytes, "Bài tập.txt")

    assert list(rar_tmp.iterdir()) == []
