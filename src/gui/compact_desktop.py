import flet as ft
import os
import sys
import logging

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.append(project_root)

from gui.app_controller import AppController

def main(page: ft.Page):
    logging.getLogger(__name__).critical('HELLO FROM COMPACT DESKTOP!!!')
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(project_root, "assets"))
