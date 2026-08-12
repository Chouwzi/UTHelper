import flet as ft
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.append(project_root)

from gui.app_controller import AppController

def main(
    page: ft.Page,
    *,
    activation_broker=None,
    force_visible: bool = False,
    error_reporter=None,
) -> AppController:
    options = {
        "activation_broker": activation_broker,
        "force_visible": force_visible,
    }
    if error_reporter is not None:
        options["error_reporter"] = error_reporter
    return AppController(page, **options)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(project_root, "assets"))
