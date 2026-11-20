"""Reserve one output per input and publish completed translations."""
import os
import shutil
from stat import S_ISREG
import tempfile
from contextlib import ExitStack, contextmanager
from itertools import count
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
def _lock(path: Path, name: str) -> Iterator[None]:
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
            raise RuntimeError(f"A translation is already using {name}.") from exc
        yield


def _project_state(root: Path, name: str) -> Path:
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError("Invalid translation output name.")
    project = root.resolve() / ".latextrans" / name
    if project.is_symlink():
        raise ValueError(f"Invalid diagnostics directory: {project}")
    project.mkdir(mode=0o700, parents=True, exist_ok=True)
    return project


@contextmanager
def translation_slot(root: Path, name: str) -> Iterator[Path]:
    project = _project_state(root, name)
    destination = root / name
    # A running older client may still hold the per-paper lock at its original path.
    legacy_lock = project.parent / f"{name}.lock"
    with ExitStack() as locks:
        locks.enter_context(_lock(project / "translation.lock", name))
        if legacy_lock.is_file():
            locks.enter_context(_lock(legacy_lock, name))
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError(f"Invalid output directory: {destination}")
        yield destination


def _numbered_directory(parent: Path, prefix: str) -> Path:
    for number in count(1):
        directory = parent / f"{prefix}-{number:03d}"
        try:
            directory.mkdir(mode=0o700)
            return directory
        except FileExistsError:
            continue


@contextmanager
def recompile_directory(run: Path) -> Iterator[Path]:
    # TeX and bibliography tools may also write into the retained source tree.
    with _lock(run / "recompile.lock", str(run)):
        yield _numbered_directory(run, "recompile")


@contextmanager
def translation_workspace(root: Path, output_name: str) -> Iterator[Path]:
    """Reserve a numbered run under the same name used for publication."""
    run = _numbered_directory(_project_state(root, output_name), "run")
    workspace = run / "work"
    workspace.mkdir()
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace)
        if not any(run.iterdir()):
            run.rmdir()


def preserve_diagnostics(attempts: list, workspace: Path) -> None:
    """Retain engine logs and partial PDFs even if this workspace is discarded."""
    destination = workspace.parent
    saved_any = False
    for attempt in attempts:
        for field in ("log_path", "pdf_path"):
            if field == "pdf_path" and attempt["status"] == "success" and attempt.get("delivery_status", "ready") == "ready":
                continue
            path = Path(attempt[field]) if attempt.get(field) else None
            if path is None or not path.is_relative_to(workspace) or not path.is_file():
                continue
            build = workspace / ".latextrans-build"
            relative = path.relative_to(build) if path.is_relative_to(build) else path.relative_to(workspace)
            saved = destination / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            attempt[field] = str(saved)
            saved_any = True
    if saved_any:
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
