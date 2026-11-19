import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Optional, TypedDict

from .utils import find_main_tex_file


CompileStatus = Literal["success", "pdf_with_errors", "failed"]


class CompileAttempt(TypedDict):
    engine: str
    exit_code: Optional[int]
    status: CompileStatus
    pdf_path: Optional[str]
    log_path: str


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


def run_latexmk(engine: str, tex_file: Path, output_dir: Path, *, publish=False) -> CompileAttempt:
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
            process = subprocess.run(cmd, cwd=tex_file.parent, stdout=output, stderr=subprocess.STDOUT)
            exit_code = process.returncode
        except OSError as exc:
            output.write(str(exc).encode("utf-8"))
            exit_code = None
        pdf = _reported_pdf(report, tex_file.parent, build_dir)

    status: CompileStatus = "failed"
    if pdf:
        status = "success" if exit_code == 0 else "pdf_with_errors"
        if publish and exit_code == 0:
            source = Path(pdf)
            pdf = str(source.replace(output_dir / source.name))
    exit_detail = "could not start latexmk" if exit_code is None else f"latexmk exit {exit_code}"
    print(f"{engine}: {status} ({exit_detail}), log: {log}")
    if pdf:
        print(f"PDF: {pdf}")
    return CompileAttempt(engine=engine, exit_code=exit_code, status=status, pdf_path=pdf, log_path=str(log))


class LaTexCompiler:
    def __init__(self, output_latex_dir: str):
        self.output_latex_dir = output_latex_dir
        self.attempts: list[CompileAttempt] = []

    def compile(self):
        return self._compile(("pdflatex", "xelatex"))

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
            print("No main TeX file found.")
            return None
        tex_file = Path(main_file).resolve()
        parent = Path(output_dir or self.output_latex_dir).resolve()
        for engine in engines:
            attempt = run_latexmk(engine, tex_file, parent, publish=output_dir is not None)
            self.attempts.append(attempt)
            if attempt["status"] == "success":
                marker.write_text("Compilation successful\n", encoding="utf-8")
                return attempt["pdf_path"]
        return None
