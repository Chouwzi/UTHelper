import sys
import logging
import os

logging.basicConfig(level=logging.DEBUG, filename=os.path.expanduser('~\\AppData\\Roaming\\UTHelper\\logs\\test_app.log'))
logging.info(f'TEST APP BOOTING FROM {__file__}')

import flet as ft
def main(page: ft.Page):
    logging.info('PAGE OPENED!')
    page.add(ft.Text('HELLO FROM TEST!'))
    page.update()

try:
    logging.info('CALLING ft.run')
    ft.run(main=main, view=ft.AppView.WEB_BROWSER, port=8561)
except Exception as e:
    logging.exception('ERROR IN ft.run')
