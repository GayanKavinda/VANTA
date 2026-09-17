# VANTA

VANTA is a desktop download manager for analyzing web pages, discovering downloadable resources, and selecting what to download. It is built with Python, PySide6, `httpx`, and an async Qt event loop.

**Current version:** `2.0.0-dev`
**Status:** Active development

## What it does

- Analyzes URLs and identifies direct, protected, or generic sources.
- Discovers links and candidate resources from supported pages.
- Classifies, groups, filters, sorts, scores, and resolves discovered resources.
- Prepares selected resources for download with filename and duplicate handling.
- Streams downloads with byte-accurate progress, speed reporting, pause, resume, and cancel support.
- Schedules concurrent downloads through queue and lifecycle controllers.
- Persists settings, download history, and task state in SQLite.
- Provides a focused dark desktop interface with home, analysis, downloads, history, and settings views.
- Applies URL and download security checks before network and file operations.

## Development status

The application is in the V2.0 development cycle. The current codebase includes the V2.0 analysis, resource discovery, download preparation, queue integration, scheduler handoff, execution lifecycle, and active-concurrency work.

Latest local test run:

```text
1201 passed, 1 failed
```

The remaining failure is `tests/test_scheduler.py::test_completion_promotes_next`. It exposes a scheduler promotion issue: after one active task completes, the scheduler reports one active task instead of restoring the configured concurrency of three. The suite also reports pending async task cleanup in that scenario.

## Technology

- Python 3.14
- PySide6
- `httpx`
- `qasync`
- SQLAlchemy with SQLite
- PyInstaller
- pytest and pytest-asyncio

## Getting started

Create and activate a virtual environment, then install the runtime and development dependencies:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

Start VANTA with:

```powershell
python main.py
```

The application creates its runtime data and log directories on first start.

## Testing

Run the complete suite:

```powershell
python -m pytest tests/ -v
```

Run the scheduler tests while working on queue or concurrency behavior:

```powershell
python -m pytest tests/test_scheduler.py tests/test_v200_phase42_scheduler_concurrency.py -q
```

## Build a Windows executable

```powershell
pip install pyinstaller
pyinstaller --noconfirm --windowed --name VANTA --add-data "assets;assets" --add-data "data;data" main.py
```

## Project structure

```text
app/
|-- core/       Task models, downloader, queue controller, scheduler, and validation
|-- database/   SQLite connection, ORM models, and repositories
|-- services/   Analysis, discovery, resource intelligence, download workflow, and security
|-- sources/    Source adapters, parsing, link classification, and candidate scoring
|-- ui/         Main window, pages, download review, and reusable widgets
`-- utils/      Paths, constants, and logging
assets/         Qt styles and application icons
data/           Runtime SQLite database files
logs/           Application logs
tests/          Unit, integration, security, UI, and lifecycle regression tests
main.py         Application entry point
```

## Design principles

- **Accurate progress:** progress and speed come from actual bytes transferred.
- **Separated responsibilities:** the UI coordinates user actions; services and core components own application behavior.
- **Composable discovery:** source adapters and analysis services can evolve without a large conditional chain.
- **Explicit lifecycle state:** queued, active, paused, completed, cancelled, and failed states are tracked and persisted.
- **Security by default:** URLs, redirects, downloads, and filesystem targets are validated before use.
- **Honest failure handling:** unsupported, protected, or unresolved sources produce clear outcomes rather than fabricated results.

## Repository guidance

See [AGENTS.md](AGENTS.md) for the supported setup, test, run, and packaging commands.

Download only from sources you are authorized to access.
