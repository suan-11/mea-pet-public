"""Project root and path helpers."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# meapet/ is inside project root (or sys._MEIPASS when frozen)
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

_LEGACY_HOME_DATA = Path.home() / ".meapet"
_MIGRATION_DONE = False


def is_frozen() -> bool:
    """True when running under PyInstaller (onedir/onefile)."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def project_root() -> str:
    return str(PROJECT_ROOT)


def project_path(*parts: str) -> str:
    return str(PROJECT_ROOT.joinpath(*parts))


def get_install_dir() -> str:
    """Directory that contains the frozen executable, or the project root."""
    if is_frozen():
        return str(Path(sys.executable).resolve().parent)
    return project_root()


def get_data_dir() -> str:
    """Return a writable directory for runtime data (logs, cache, config saves).

    Source / development mode: ``PROJECT_ROOT``.
    PyInstaller onedir (portable): ``sys._MEIPASS`` (``dist/MeaPet/_internal``),
    so config, memory DB, caches and logs travel with the distribution folder.
    """
    if is_frozen():
        data_dir = Path(sys._MEIPASS)
    else:
        data_dir = PROJECT_ROOT
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return str(data_dir)


def data_path(*parts: str) -> str:
    """Join path parts under :func:`get_data_dir`."""
    return str(Path(get_data_dir()).joinpath(*parts))


def migrate_legacy_home_data() -> list[str]:
    """Copy files from legacy ``~/.meapet`` into the portable data dir once.

    Only fills missing targets so a newer ``_internal`` copy is never overwritten.
    Returns a list of human-readable migration notes (empty if nothing moved).
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE or not is_frozen():
        return []
    _MIGRATION_DONE = True

    if not _LEGACY_HOME_DATA.is_dir():
        return []

    dest_root = Path(get_data_dir())
    notes: list[str] = []
    candidates = (
        "config.json",
        "openclaw_device_identity.json",
        "mea_memory.db",
        "mea_memory.db-wal",
        "mea_memory.db-shm",
        "chat_errors.log",
        "meapet_boot.log",
        "meapet_fault.log",
    )
    for name in candidates:
        src = _LEGACY_HOME_DATA / name
        dest = dest_root / name
        if not src.is_file() or dest.exists():
            continue
        try:
            shutil.copy2(src, dest)
            notes.append(f"migrated {name} from ~/.meapet")
        except OSError:
            continue

    src_logs = _LEGACY_HOME_DATA / "logs"
    dest_logs = dest_root / "logs"
    if src_logs.is_dir():
        try:
            dest_logs.mkdir(parents=True, exist_ok=True)
            for item in src_logs.iterdir():
                if not item.is_file():
                    continue
                target = dest_logs / item.name
                if target.exists():
                    continue
                shutil.copy2(item, target)
                notes.append(f"migrated logs/{item.name} from ~/.meapet")
        except OSError:
            pass
    return notes
