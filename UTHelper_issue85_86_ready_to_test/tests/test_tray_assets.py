import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


def test_tray_icon_resolver_falls_back_to_frozen_assets_dir(monkeypatch, tmp_path):
    import gui.tray as tray

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    icon = assets_dir / "icon.ico"
    icon.write_bytes(b"ico")

    monkeypatch.setattr(tray, "BASE_DIR", tmp_path)

    assert tray._resolve_tray_icon_path() == str(icon)
