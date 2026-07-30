import os
import sys

flet_path = r'build\windows\site-packages\flet\messaging\flet_socket_server.py'
with open(flet_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Inject logger
injection = '''
import traceback
def _debug_log(msg):
    try:
        with open(r'C:\\temp\\flet_socket.log', 'a', encoding='utf-8') as lf:
            lf.write(str(msg) + "\\n")
    except:
        pass
_debug_log("FLET SOCKET SERVER MODULE LOADED!")
'''

content = injection + content

# Replace standard print or logging if needed, but let's just inject _debug_log calls in on_message
content = content.replace('logging.info(f"Connected new socket client: {client_addr}")', 
    'logging.info(f"Connected new socket client: {client_addr}"); _debug_log(f"Connected new socket client: {client_addr}")')

content = content.replace('def on_message(message: str):',
    'def on_message(message: str):\n                _debug_log(f"Got message: {message[:100]}")')

content = content.replace('def on_session_created(session_data):',
    'def on_session_created(session_data):\n                _debug_log("on_session_created started")')

content = content.replace('self.on_session_created(session_data)',
    '_debug_log("Before calling self.on_session_created"); self.on_session_created(session_data); _debug_log("After calling self.on_session_created")')

with open(flet_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Injected debug logging into flet_socket_server.py")
