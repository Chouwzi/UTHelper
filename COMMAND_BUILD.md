pip install flet pyinstaller httpx pydantic beautifulsoup4 certifi python-dateutil

cd src
pyinstaller main.py --name UTHelper --noconsole --onedir --icon assets/icon.ico --paths . --add-data "assets;assets" --collect-submodules diagnostics --collect-submodules core --collect-submodules gui --collect-submodules platform_utils --collect-submodules notifiers --collect-all flet --collect-all flet_desktop --hidden-import certifi --hidden-import config --hidden-import models --clean --noconfirm

copy /y dist\UTHelper\UTHelper.exe dist\UTHelper\UTHelperAutostart.exe