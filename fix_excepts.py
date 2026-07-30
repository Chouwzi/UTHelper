import re
import pathlib

for f in pathlib.Path('src').rglob('*.py'):
    content = f.read_text(encoding='utf-8')
    # Capture the indentation before 'pass'
    new_content = re.sub(
        r'(except Exception:)\n(\s+)pass',
        r'\1\n\2import logging as _fb_log\n\2_fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)',
        content
    )
    if new_content != content:
        f.write_text(new_content, encoding='utf-8')
        print(f"Updated {f}")
