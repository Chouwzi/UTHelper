"""Verify the pinned Flet template and its generated Flutter runner source."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import sys
import zipfile

if __package__:
    from scripts.prepare_flet_diagnostics_template import (
        InvalidPreparedTemplate,
        PreparedTemplate,
        TARGET_MEMBER,
        verify_template,
    )
else:
    from prepare_flet_diagnostics_template import (
        InvalidPreparedTemplate,
        PreparedTemplate,
        TARGET_MEMBER,
        verify_template,
    )


def verify_flutter_diagnostics(
    template_zip: Path,
    project_root: Path,
) -> PreparedTemplate:
    """Require generated main.dart to be byte-identical to the reviewed source."""

    prepared = verify_template(Path(template_zip))
    try:
        with zipfile.ZipFile(template_zip) as archive:
            expected = archive.read(TARGET_MEMBER)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise InvalidPreparedTemplate("prepared template runner is unreadable") from exc
    generated_path = Path(project_root) / "lib" / "main.dart"
    try:
        generated = generated_path.read_bytes()
    except OSError as exc:
        raise InvalidPreparedTemplate("generated Flutter runner is missing") from exc
    if sha256(generated).digest() != sha256(expected).digest():
        raise InvalidPreparedTemplate("generated Flutter runner does not match the patch")
    return prepared


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prepared = verify_flutter_diagnostics(args.template, args.project_root)
    except InvalidPreparedTemplate:
        print("Flutter diagnostics verification failed.", file=sys.stderr)
        return 3
    print(f"Verified Flutter diagnostics template: {prepared.output_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
