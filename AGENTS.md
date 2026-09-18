# Commands for this VANTA project

## Setup

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

## Run the application

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

## Run tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest tests/ -v
```

## Build executable

```powershell
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --noconfirm --windowed --name VANTA --icon=assets/icons/vanta.ico --version-file=assets/VANTA_version_info.txt --manifest=assets/VANTA.manifest --add-data "assets;assets" --add-data "data;data" main.py
```

The same options are applied via the committed `VANTA.spec` (icon, version, manifest are wired into the `EXE()` call), which is the source of truth for reproducible builds:

```powershell
pyinstaller VANTA.spec
```
