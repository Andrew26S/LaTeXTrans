"""Reserve one output per input and publish completed translations."""
import os
import shutil
from stat import S_ISREG
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.utils.progress import log_message


def pdf_revision(pdf: Path) -> str:
    try:
        stat = pdf.stat()
    except FileNotFoundError:
        return ""
    if not S_ISREG(stat.st_mode):
        return ""
    return f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    # The OS releases the lock even when a CLI process or GUI session crashes.
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"A translation is already using {path.stem}.") from exc
        yield


@contextmanager
def translation_slot(root: Path, name: str) -> Iterator[Path]:
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError("Invalid translation output name.")
    state = root / ".latextrans"
    state.mkdir(parents=True, exist_ok=True)
    destination = root / name
    with _lock(state / f"{name}.lock"):
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError(f"Invalid output directory: {destination}")
        yield destination


def preserve_diagnostics(attempts: list, workspace: Path, root: Path) -> None:
    """Retain engine logs and partial PDFs even if this workspace is discarded."""
    destination = None
    for attempt in attempts:
        for field in ("log_path", "pdf_path"):
            if field == "pdf_path" and attempt["status"] == "success":
                continue
            path = Path(attempt[field]) if attempt.get(field) else None
            if path is None or not path.is_relative_to(workspace) or not path.is_file():
                continue
            if destination is None:
                destination = Path(tempfile.mkdtemp(prefix="diagnostics-", dir=root / ".latextrans"))
            saved = destination / path.relative_to(workspace)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            attempt[field] = str(saved)
    if destination:
        log_message(f"Saved compilation diagnostics in {destination}")


def publish_translation(workspace: Path, destination: Path, pdf_name: str) -> None:
    """Replace generated files only. Preserve unrelated files in the paper directory."""
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workspace.parent) as tmp:
        backup = Path(tmp)
        installed = []
        try:
            for incoming in workspace.iterdir():
                if incoming.name == pdf_name:
                    continue
                target = destination / incoming.name
                if target.exists() or target.is_symlink():
                    target.rename(backup / target.name)
                incoming.rename(target)
                installed.append(target)
            (workspace / pdf_name).replace(destination / pdf_name)
        except BaseException:
            for target in reversed(installed):
                target.rename(workspace / target.name)
            for original in backup.iterdir():
                original.rename(destination / original.name)
            raise
