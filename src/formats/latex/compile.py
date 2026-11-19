import re
import subprocess
import tempfile
from pathlib import Path

from .utils import find_main_tex_file


class LaTexCompiler:
    def __init__(self, output_latex_dir: str):
        self.output_latex_dir = output_latex_dir

    def compile(self):
        return self._compile(("pdflatex", "xelatex"))

    def compile_ja(self):
        return self._compile(("lualatex",))

    def compile_source(self, pdf_dir):
        return self._compile(("pdflatex", "xelatex"), Path(pdf_dir or self.output_latex_dir))

    def _compile(self, engines, output_dir=None):
        main_file = find_main_tex_file(self.output_latex_dir)
        if not main_file:
            print("No main TeX file found.")
            return None
        tex_file = Path(main_file).resolve()
        for engine in engines:
            if output_dir is None:
                build_dir = Path(self.output_latex_dir, f"build_{engine}").resolve()
                try:
                    build_dir.mkdir()
                except FileExistsError:
                    # Source trees may already contain a build of this paper.
                    build_dir = Path(tempfile.mkdtemp(prefix=f"build_{engine}_", dir=build_dir.parent))
            else:
                build_dir = output_dir.resolve()
                build_dir.mkdir(parents=True, exist_ok=True)

            print(f"Compiling with {engine} in {build_dir}...")
            pdf = self._run_latexmk(engine, tex_file, build_dir)
            if pdf is not None:
                Path(self.output_latex_dir, "success.txt").write_text("Compilation successful\n", encoding="utf-8")
                return str(pdf)
        return None

    @staticmethod
    def _run_latexmk(engine, tex_file, build_dir):
        # The dependency report names the final PDF, including for XeLaTeX's XDV workflow.
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = Path(tmp_dir, "targets.mk")
            cmd = [
                "latexmk", f"-{engine}", "-interaction=nonstopmode", "-file-line-error",
                "-synctex=1", "-f", "-g", f"-outdir={build_dir}", "-deps-escape=none",
                f"-deps-out={report}", str(tex_file),
            ]
            result = subprocess.run(cmd, cwd=tex_file.parent, capture_output=True)
            if result.returncode:
                print(f"{engine} failed (latexmk exit {result.returncode}). See {build_dir} for logs.")
                return None
            if report.is_file():
                for line in report.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#===End dependents"):
                        break
                    target = re.match(r"^([^#\s].*?\.pdf)\s*:", line, re.IGNORECASE)
                    if target:
                        pdf = (tex_file.parent / target[1]).resolve()
                        if pdf.is_relative_to(build_dir) and pdf.is_file() and pdf.stat().st_size:
                            return pdf
            print(f"{engine} did not report a nonempty output PDF in {build_dir}.")
            return None
