import sys
import logging
import traceback

try:
    log_file = open(r'C:\Users\Chouwzi\AppData\Local\flet_debug.log', 'w', encoding='utf-8')
    sys.stdout = log_file
    sys.stderr = log_file

    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.info("Python script started.")

    import flet as ft
    import time

    def main(page: ft.Page):
        print("Page session created!", flush=True)
        logging.info("Page session created in logging!")
        page.title = "Test Flet App"
        page.add(ft.Text(f"Flet loaded successfully at {time.time()}!"))
        print("UI added!", flush=True)
        logging.info("UI added!")

    print("Starting flet app...", flush=True)
    logging.info("Calling ft.app()")
    ft.app(target=main)
    print("Flet app exited normally.", flush=True)
    logging.info("Exited normally.")
except Exception as e:
    with open(r'C:\Users\Chouwzi\AppData\Local\flet_debug_crash.log', 'w', encoding='utf-8') as f:
        f.write(traceback.format_exc())
