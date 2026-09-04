# VANTA

A desktop download manager built with Python 3.14, PySide6, and httpx.

## Features

- **Source analysis** — Automatically detects supported URLs (direct file downloads, protected sources)
- **Real downloads** — Streams to disk in chunks with byte-accurate progress and speed
- **Download management** — Pause, resume, cancel with real state tracking
- **SQLite persistence** — Download history and settings survive app restarts
- **Dark design system** — Minimal, high-contrast dark theme
- **Native folder selection** — Choose download locations via Qt file dialogs
- **Settings** — Configure concurrent downloads, speed limits, theme
- **Error handling** — Clear error messages for unsupported or protected sources

## Technology Stack

- Python 3.14
- PySide6 (Qt UI framework)
- httpx (async HTTP client)
- qasync (Qt + asyncio integration)
- SQLAlchemy (SQLite ORM)
- PyInstaller (packaging)

## Setup

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

## Test

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest tests/ -v
```

## Build Executable

```powershell
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --noconfirm --windowed --name VANTA --add-data "assets;assets" --add-data "data;data" main.py
```

## Architecture

```
VANTA/
├── app/
│   ├── core/
│   │   ├── app_state.py       # Shared application state (singleton)
│   │   ├── downloader.py      # Async download manager with chunked streaming
│   │   ├── task_manager.py    # DownloadTask model + TaskStatus enum
│   │   ├── file_manager.py    # File operations + unique path generation
│   │   └── models.py          # AnalysisResult, DownloadFile, DownloadError dataclasses
│   │
│   ├── database/
│   │   ├── connection.py      # SQLAlchemy engine + session factory
│   │   ├── models.py          # DownloadRecord, SettingRecord
│   │   └── repositories.py    # CRUD operations for downloads and settings
│   │
│   ├── services/
│   │   ├── analyzer.py        # Orchestrates URL analysis via adapters
│   │   ├── source_detector.py # Finds the right adapter for a URL
│   │   ├── settings_service.py# Settings load/save with SQLite
│   │   ├── download_service.py # Orchestrates analyze → download flow
│   │   └── persistence_service.py # Auto-saves task state to DB
│   │
│   ├── sources/
│   │   ├── base.py            # BaseSourceAdapter ABC
│   │   ├── direct.py          # Direct file download adapter
│   │   ├── protected.py       # Protected/login-required source adapter
│   │   └── generic.py         # Fallback adapter
│   │
│   ├── ui/
│   │   ├── main_window.py     # QMainWindow with sidebar + stacked pages
│   │   ├── pages/
│   │   │   ├── home_page.py   # URL input + analysis result display
│   │   │   ├── downloads_page.py # Active downloads with cards
│   │   │   ├── history_page.py  # Past downloads from SQLite
│   │   │   └── settings_page.py  # Settings with native dialogs
│   │   └── widgets/
│   │       ├── sidebar.py     # Navigation sidebar
│   │       ├── sidebar_button.py  # Sidebar navigation button
│   │       └── download_card.py   # Reusable download progress card
│   │
│   └── utils/
│       ├── constants.py       # App constants and paths
│       └── logger.py          # Structured logging to file + console
│
├── assets/
│   ├── styles/main.qss         # VANTA design system
│   └── icons/
├── data/
│   └── vanta.db                # SQLite database (created on first run)
├── logs/
│   └── vanta.log               # Application log
├── tests/
│   ├── test_validators.py      # URL validation tests
│   ├── test_analyzer.py        # Source adapter + detector tests
│   ├── test_downloader.py      # Real download tests (local HTTP server)
│   ├── test_models.py          # Data model tests
│   └── test_database.py        # SQLite persistence tests
├── main.py
├── requirements.txt
├── requirements-dev.txt
├── AGENTS.md
└── README.md
```

## Design Principles

- **No fake progress** — Progress is computed from actual bytes downloaded
- **UI never downloads** — UI emits signals, services handle logic, UI receives results
- **Source adapters** — New sources added by implementing `BaseSourceAdapter`, no if/else chains
- **Protected sources handled gracefully** — Login-required or Cloudflare-protected pages show clear error messages, never fake results
- **State persistence** — Download task state and settings saved to SQLite on every state change
- **Resume support** — Detects `Range` request support and resumes from local file size

## License

Built for educational and personal use. Download only from authorized sources.
