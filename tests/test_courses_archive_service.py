"""Tests for path-safe archive inspection and single-entry extraction."""

from __future__ import annotations

import io
import stat
import subprocess
import tarfile
import tempfile
import zipfile

import pytest

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

    def __init__(self, total_size: int) -> None:
        self._remaining = total_size
        self.bytes_read = 0

    def __enter__(self) -> "_CountingStream":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        to_emit = self._remaining if size < 0 else min(size, self._remaining)
        self._remaining -= to_emit
        self.bytes_read += to_emit
        return b"A" * to_emit


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

    unsafe_paths = (
        "a/../b",
        "../x",
        "a\\..\\..\\b",
        "dir/..",
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


def test_archive_service_corrupt_archives_return_unsupported_cleanly():
    svc = ArchiveService()
    for name, expected_fmt in (
        ("broken.zip", "zip"),
        ("broken.tar", "tar"),
        ("broken.tar.gz", "tar"),
        ("broken.tgz", "tar"),
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


def test_archive_service_skips_and_rejects_zip_symlinks():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notes.txt", b"ok")
        link_info = zipfile.ZipInfo("secret_link")
        link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link_info, b"/etc/passwd")

    raw = buf.getvalue()
    svc = ArchiveService()
    tree = svc.inspect_bytes("with_symlink.zip", raw)
    assert tree.supported is True
    assert [c.name for c in tree.root.children] == ["notes.txt"]
    assert svc.extract_entry_bytes("with_symlink.zip", raw, "notes.txt") == b"ok"
    with pytest.raises(ArchiveSecurityError):
        svc.extract_entry_bytes("with_symlink.zip", raw, "secret_link")


def test_find_rar_cli_alias_and_lookup(monkeypatch):
    assert find_rar_cli is find_windows_rar_cli
    monkeypatch.setattr(
        "core.courses.archive_service.shutil.which",
        lambda name: r"C:\Program Files\7-Zip\7z.exe" if name == "7z" else None,
    )
    assert find_windows_rar_cli() == r"C:\Program Files\7-Zip\7z.exe"


def test_archive_service_rar_7z_slt_parsing_and_safe_extraction(monkeypatch, tmp_path):
    temp_dir = tmp_path / "rar_tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TEMP", str(temp_dir))

    seen_calls: list[tuple[list[str], dict[str, object]]] = []
    slt_stdout = (
        "7-Zip 24.08 (x64)\n\n"
        "Listing archive: C:\\Users\\student\\AppData\\Local\\Temp\\tmp_test.rar\n\n"
        "--\n"
        "Path = C:\\Users\\student\\AppData\\Local\\Temp\\tmp_test.rar\n"
        "Type = Rar5\n"
        "Physical Size = 256\n"
        "Encrypted = -\n\n"
        "----------\n"
        "Path = Chuong1\n"
        "Folder = +\n"
        "Size = 0\n\n"
        "Path = Chuong1\\Sub\n"
        "Folder = +\n"
        "Size = 0\n\n"
        "Path = Chuong1\\Sub\\Bai1.txt\n"
        "Folder = -\n"
        "Size = 15\n"
        "Encrypted = -\n\n"
        "Path = README.md\n"
        "Folder = -\n"
        "Size = 8\n"
        "Encrypted = -\n"
    )

    def _fake_run(cmd, **kwargs):
        seen_calls.append((list(cmd), kwargs))
        if cmd[1] == "l":
            return subprocess.CompletedProcess(cmd, 0, stdout=slt_stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"Xin chao tu RAR", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    svc = ArchiveService(rar_tool_finder=lambda: r"C:\Program Files\7-Zip\7z.exe")

    tree = svc.inspect_bytes("lecture.rar", b"Rar!-stub")
    assert tree.supported is True
    assert tree.format_name == "rar"
    assert tree.total_files == 2
    assert [n.name for n in tree.root.children] == ["Chuong1", "README.md"]
    chuong1 = tree.root.children[0]
    assert chuong1.is_dir is True
    assert [c.name for c in chuong1.children] == ["Sub"]
    assert chuong1.children[0].children[0].size == 15

    list_cmd, list_kwargs = seen_calls[0]
    assert list_cmd[:5] == [r"C:\Program Files\7-Zip\7z.exe", "l", "-slt", "-p-", "--"]
    assert list_kwargs.get("stdin") is subprocess.DEVNULL
    assert list_kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)

    extracted = svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "Chuong1/Sub/Bai1.txt")
    assert extracted == b"Xin chao tu RAR"

    ext_cmd, ext_kwargs = seen_calls[1]
    assert ext_cmd[:5] == [r"C:\Program Files\7-Zip\7z.exe", "x", "-so", "-p-", "--"]
    assert ext_cmd[-1] == "Chuong1/Sub/Bai1.txt"
    assert ext_kwargs.get("stdin") is subprocess.DEVNULL
    assert ext_kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert list(temp_dir.iterdir()) == []


def test_archive_service_rar_unrar_lb_p_parsing_and_flag_injection_guard(monkeypatch):
    seen_calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        seen_calls.append(list(cmd))
        if cmd[1] == "lb":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="Chuong1\nChuong1\\Bai1.txt\nREADME.md\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout=b"unrar-content", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    svc = ArchiveService(rar_tool_finder=lambda: r"C:\Program Files\WinRAR\UnRAR.exe")

    tree = svc.inspect_bytes("lecture.rar", b"Rar!-stub")
    assert tree.supported is True
    assert [n.name for n in tree.root.children] == ["Chuong1", "README.md"]
    assert tree.root.children[0].is_dir is True
    assert [c.name for c in tree.root.children[0].children] == ["Bai1.txt"]

    data = svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", "Chuong1/Bai1.txt")
    assert data == b"unrar-content"
    assert seen_calls[0][:4] == [r"C:\Program Files\WinRAR\UnRAR.exe", "lb", "-p-", "--"]
    assert seen_calls[1][:5] == [r"C:\Program Files\WinRAR\UnRAR.exe", "p", "-inul", "-p-", "--"]
    assert seen_calls[1][-1] == "Chuong1/Bai1.txt"

    for unsafe_member in ("-rf", "@secret.txt"):
        with pytest.raises(ArchiveSecurityError):
            svc.extract_entry_bytes("lecture.rar", b"Rar!-stub", unsafe_member)


def test_archive_service_rar_zipslip_and_failure_cleanup(monkeypatch, tmp_path):
    temp_dir = tmp_path / "rar_err_tmp"
    temp_dir.mkdir()
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TEMP", str(temp_dir))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    # 1. ZipSlip in RAR listing must raise ArchiveSecurityError and clean temp file
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout="----------\nPath = ../evil.txt\nFolder = -\nSize = 4\n",
            stderr="",
        ),
    )
    svc = ArchiveService(rar_tool_finder=lambda: "7z")
    with pytest.raises(ArchiveSecurityError):
        svc.inspect_bytes("evil.rar", b"Rar!-stub")
    assert list(temp_dir.iterdir()) == []

    # 2. Timeout -> supported=False gracefully + temp file cleanup
    def _raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=15)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    res_timeout = svc.inspect_bytes("slow.rar", b"Rar!-stub")
    assert res_timeout.supported is False
    assert res_timeout.format_name == "rar"
    assert res_timeout.unsupported_reason.strip() != ""
    assert list(temp_dir.iterdir()) == []

    # 3. Non-zero CLI failure -> supported=False gracefully + temp file cleanup
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 2, stdout="", stderr="ERROR: Cannot open archive"
        ),
    )
    res_fail = svc.inspect_bytes("broken.rar", b"Rar!-stub")
    assert res_fail.supported is False
    assert res_fail.format_name == "rar"
    assert list(temp_dir.iterdir()) == []

    # 4. Encrypted RAR (7z reports Encrypted = +) -> supported=False gracefully + cleanup
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout="----------\nPath = secret.txt\nFolder = -\nSize = 10\nEncrypted = +\n",
            stderr="",
        ),
    )
    res_enc = svc.inspect_bytes("encrypted.rar", b"Rar!-stub")
    assert res_enc.supported is False
    assert res_enc.format_name == "rar"
    assert res_enc.unsupported_reason.strip() != ""
    assert list(temp_dir.iterdir()) == []



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

    unknown = svc.inspect_bytes("archive.7z", b"7z\xbc\xaf\x27\x1c")
    assert unknown.supported is False
    assert unknown.format_name == "unknown"
    with pytest.raises(ValueError):
        svc.extract_entry_bytes("archive.7z", b"7z", "a.txt")
    with pytest.raises(ValueError):
        ArchiveService(max_entries=-1)


def test_archive_service_rar_enforces_resource_caps(monkeypatch, tmp_path):
    temp_dir = tmp_path / "rar_cap_tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "----------\n"
                "Path = big.txt\nFolder = -\nSize = 2048\n\n"
                "Path = small.txt\nFolder = -\nSize = 10\n"
            ),
            stderr="",
        ),
    )
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_entries=1, rar_tool_finder=lambda: "7z").inspect_bytes(
            "caps.rar", b"Rar!-stub"
        )
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_uncompressed_bytes=1024, rar_tool_finder=lambda: "7z").inspect_bytes(
            "caps.rar", b"Rar!-stub"
        )
    assert list(temp_dir.iterdir()) == []

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, stdout=b"B" * 2048, stderr=b""
        ),
    )
    with pytest.raises(ArchiveSecurityError):
        ArchiveService(max_single_entry_bytes=512, rar_tool_finder=lambda: "7z").extract_entry_bytes(
            "caps.rar", b"Rar!-stub", "big.txt"
        )
    assert list(temp_dir.iterdir()) == []
