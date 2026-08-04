import flet as ft
import os
import sys
import logging

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.append(project_root)

from gui.app_controller import AppController

def main(
    page: ft.Page, *, activation_broker=None, force_visible: bool = False
) -> AppController:
    logging.getLogger(__name__).critical('HELLO FROM COMPACT DESKTOP!!!')
    return AppController(
        page,
        activation_broker=activation_broker,
        force_visible=force_visible,
    )

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(project_root, "assets"))
