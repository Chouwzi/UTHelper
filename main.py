import sys
from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parent / "src"
_source_root_text = str(_SOURCE_ROOT)
if _source_root_text not in sys.path:
    sys.path.insert(0, _source_root_text)

from src.main import main


if __name__ == '__main__':
    if sys.platform == 'win32':
        import multiprocessing
        multiprocessing.freeze_support()
    raise SystemExit(main())
