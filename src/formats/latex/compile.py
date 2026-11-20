import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, NotRequired, Optional, TypedDict

from src.utils.progress import log_message
from .utils import find_main_tex_file
from .delivery import DeliveryStatus, assess_pdf


CompileStatus = Literal["success", "pdf_with_errors", "failed"]


class CompileAttempt(TypedDict):
    engine: str
    exit_code: Optional[int]
    status: CompileStatus
    pdf_path: Optional[str]
    log_path: str
    delivery_status: NotRequired[DeliveryStatus]
    diagnostics: NotRequired[list[str]]


def _reported_pdf(report: Path, source_dir: Path, build_dir: Path) -> Optional[str]:
    # The dependency report names the final PDF, including for XeLaTeX's XDV workflow.
    if report.is_file():
        for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#===End dependents"):
                break
            target = re.match(r"^([^#\s].*?\.pdf)\s*:", line, re.IGNORECASE)
            if target:
                pdf = (source_dir / target[1]).resolve()
                if pdf.is_relative_to(build_dir) and pdf.is_file() and pdf.stat().st_size:
                    return str(pdf)
    return None


def run_latexmk(
    engine: str, tex_file: Path, output_dir: Path, *, publish=False, cwd: Optional[Path] = None,
) -> CompileAttempt:
    working_dir = tex_file.parent if cwd is None else cwd
    build_dir = output_dir / f"build_{engine}"
    try:
        build_dir.mkdir(parents=True)
    except FileExistsError:
        # Keep previous diagnostics and accept only files created by this attempt.
        build_dir = Path(tempfile.mkdtemp(prefix=f"build_{engine}_", dir=output_dir))
    log = build_dir / "latexmk-output.txt"
    with tempfile.TemporaryDirectory() as tmp_dir, log.open("wb") as output:
        report = Path(tmp_dir, "targets.mk")
        cmd = [
            "latexmk", f"-{engine}", "-interaction=nonstopmode", "-file-line-error",
            "-synctex=1", "-f", "-g", f"-outdir={build_dir}", "-deps-escape=none",
            f"-deps-out={report}", str(tex_file),
        ]
        try:
            process = subprocess.run(cmd, cwd=working_dir, stdout=output, stderr=subprocess.STDOUT)
            exit_code = process.returncode
        except OSError as exc:
            output.write(str(exc).encode("utf-8"))
            exit_code = None
        pdf = _reported_pdf(report, working_dir, build_dir)

    status: CompileStatus = "failed"
    if pdf:
        status = "success" if exit_code == 0 else "pdf_with_errors"
        if publish and exit_code == 0:
            source = Path(pdf)
            target = output_dir / source.name
            sidecar = source.with_suffix(".synctex.gz")
            published_sidecar = target.with_suffix(".synctex.gz")
            published_sidecar.unlink(missing_ok=True)
            pdf = str(source.replace(target))
            if sidecar.is_file():
                sidecar.replace(published_sidecar)
    exit_detail = "could not start latexmk" if exit_code is None else f"latexmk exit {exit_code}"
    log_message(f"{engine}: {status} ({exit_detail}), log: {log}")
    if pdf:
        log_message(f"PDF: {pdf}")
    return CompileAttempt(engine=engine, exit_code=exit_code, status=status, pdf_path=pdf, log_path=str(log))


class LaTexCompiler:
    def __init__(self, output_latex_dir: str, build_root: Optional[str] = None):
        self.output_latex_dir = output_latex_dir
        self.build_root = Path(build_root or output_latex_dir)
        self.attempts: list[CompileAttempt] = []

    def compile(self):
        return self._compile(("xelatex",))

    def compile_ja(self):
        return self._compile(("lualatex",))

    def compile_source(self, pdf_dir):
        return self._compile(("pdflatex", "xelatex"), Path(pdf_dir or self.output_latex_dir))

    def _compile(self, engines, output_dir=None):
        self.attempts.clear()
        marker = Path(self.output_latex_dir, "success.txt")
        if marker.is_file() and marker.read_bytes().strip() == b"Compilation successful":
            marker.unlink()
        main_file = find_main_tex_file(self.output_latex_dir)
        if not main_file:
            log_message("No main TeX file found.")
            return None
        tex_file = Path(main_file).resolve()
        parent = (output_dir or self.build_root).resolve()
        for engine in engines:
            attempt = run_latexmk(engine, tex_file, parent, publish=output_dir is not None)
            self.attempts.append(attempt)
            if output_dir is None:
                pdf = Path(attempt["pdf_path"]) if attempt["pdf_path"] else None
                log = Path(attempt["log_path"])
                delivery, notes = assess_pdf(pdf, log, engine, attempt["exit_code"])
                attempt.update(delivery_status=delivery, diagnostics=notes)
                for note in notes:
                    log_message(note)
                if delivery not in {"ready", "ready_with_warnings"}:
                    continue
            elif attempt["status"] != "success":
                continue
            if attempt["status"] == "success":
                marker.write_text("Compilation successful\n", encoding="utf-8")
            return attempt["pdf_path"]
        return None
