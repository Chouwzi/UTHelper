"""Post-build cleanup script cho UTHelper.

Xóa các file/thư mục không cần thiết từ Flet build output
để giảm kích thước bundle từ ~150MB xuống ~50MB.

Chạy sau `flet build windows`:
    python scripts/post_build_cleanup.py [build_dir]
"""
import os
import sys
import shutil
from pathlib import Path

# ─── Patterns to remove ─────────────────────────────────────────────────────

# Directories to remove entirely
REMOVE_DIRS = [
    # Python package managers (not needed at runtime)
    "pip",
    "pip-*",
    "setuptools",
    "setuptools-*",
    "pkg_resources",
    "_distutils_hack",
    
    # Test directories
    "tests",
    "test",
    "testing",
    
    # Documentation
    "docs",
    "doc",
    
    # pywin32 bloat (COM servers, help files, etc.)
    "adodbapi",
    "isapi",
    "pythonwin",
    "win32com/servers",
    "win32com/test",
    "win32com/demos",
    "win32/test",
    "win32/demos",
    "win32/help",
    
    # Type stubs (not needed at runtime)
    "*-stubs",
    
    # Build artifacts
    "__pycache__",
    "*.dist-info",
    "*.egg-info",
    
    # Unused large packages (if present)
    "numpy/tests",
    "numpy/doc",
    "PIL/tests",
]

# File patterns to remove
REMOVE_FILE_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "*.pdb",     # Debug symbols
    "*.whl",     # Cached wheels
    "*.egg",
    "*.chm",     # Help files (pywin32)
    "*.hlp",     # Help files
    "*.cnt",     # Help file index
    "LICENSE*",
    "CHANGELOG*",
    "CHANGES*",
    "README*",
    "METADATA",
    "AUTHORS*",
    "NOTICE*",
]

# Specific files to remove
REMOVE_FILES = [
    "pythoncom*.dll",  # pywin32 COM (only needed for COM automation)
    "pywintypes*.dll", # Keep this if using win32api
]


def get_dir_size(path: Path) -> int:
    """Tính tổng kích thước thư mục (bytes)."""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except (OSError, PermissionError):
        pass
    return total


def format_size(size_bytes: int) -> str:
    """Format bytes thành human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def remove_matching_dirs(root: Path, pattern: str, stats: dict):
    """Xóa tất cả thư mục match pattern."""
    import fnmatch
    for dirpath in sorted(root.rglob('*'), reverse=True):
        if dirpath.is_dir() and fnmatch.fnmatch(dirpath.name, pattern):
            size = get_dir_size(dirpath)
            try:
                shutil.rmtree(dirpath, ignore_errors=True)
                stats['removed_bytes'] += size
                stats['removed_dirs'] += 1
                print(f"  🗑️  {dirpath.relative_to(root)} ({format_size(size)})")
            except Exception as e:
                print(f"  ⚠️  Không xóa được {dirpath}: {e}")


def remove_matching_files(root: Path, pattern: str, stats: dict):
    """Xóa tất cả files match pattern."""
    import fnmatch
    for filepath in root.rglob('*'):
        if filepath.is_file() and fnmatch.fnmatch(filepath.name, pattern):
            size = filepath.stat().st_size
            try:
                filepath.unlink()
                stats['removed_bytes'] += size
                stats['removed_files'] += 1
            except Exception:
                pass


def cleanup(build_dir: Path):
    """Chạy cleanup trên build output directory."""
    if not build_dir.exists():
        print(f"❌ Thư mục không tồn tại: {build_dir}")
        return
    
    print(f"🔧 UTHelper Post-Build Cleanup")
    print(f"   Target: {build_dir}")
    
    before_size = get_dir_size(build_dir)
    print(f"   Kích thước trước: {format_size(before_size)}")
    print()
    
    stats = {'removed_bytes': 0, 'removed_dirs': 0, 'removed_files': 0}
    
    # 1. Remove directories
    print("📁 Xóa thư mục không cần thiết...")
    for pattern in REMOVE_DIRS:
        remove_matching_dirs(build_dir, pattern, stats)
    
    # 2. Remove file patterns
    print(f"\n📄 Xóa file patterns...")
    for pattern in REMOVE_FILE_PATTERNS:
        remove_matching_files(build_dir, pattern, stats)
    
    # 3. Remove specific files
    for pattern in REMOVE_FILES:
        remove_matching_files(build_dir, pattern, stats)
    
    after_size = get_dir_size(build_dir)
    saved = before_size - after_size
    
    print(f"\n{'='*50}")
    print(f"✅ Cleanup hoàn tất!")
    print(f"   Trước:  {format_size(before_size)}")
    print(f"   Sau:    {format_size(after_size)}")
    print(f"   Tiết kiệm: {format_size(saved)} ({saved*100//before_size}%)")
    print(f"   Xóa: {stats['removed_dirs']} thư mục, {stats['removed_files']} files")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        # Default: Flet build output
        target = Path(__file__).parent.parent / "build" / "windows" / "x64" / "runner" / "Release"
        if not target.exists():
            # Try alternative path
            target = Path(__file__).parent.parent / "build" / "windows"
    
    cleanup(target)
