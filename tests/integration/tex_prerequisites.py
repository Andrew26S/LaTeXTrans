import subprocess
import tempfile
from pathlib import Path


def require_xelatex_fixture(test, *, packages=(), fonts=()):
    """Check the fixture in plain XeLaTeX before exercising reconstruction."""
    checks = [rf"\IfFileExists{{{pkg}.sty}}{{}}{{\typeout{{Missing package: {pkg}}}}}" for pkg in packages]
    checks.extend(rf"\IfFontExistsTF{{{font}}}{{}}{{\typeout{{Missing font: {font}}}}}" for font in fonts)
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "probe.tex").write_text("\n".join([
            r"\documentclass{article}\usepackage[UTF8]{ctex}", *checks,
            r"\begin{document}中文\end{document}",
        ]), encoding="utf-8")
        result = subprocess.run(
            ["xelatex", "-halt-on-error", "-interaction=nonstopmode", "probe.tex"],
            cwd=directory, capture_output=True, text=True, errors="replace",
        )
    if result.returncode:
        test.skipTest("XeLaTeX could not load the CTeX fixture:\n" + result.stdout[-800:])
    missing = [line for line in result.stdout.splitlines() if line.startswith(("Missing package:", "Missing font:"))]
    if missing:
        test.skipTest(", ".join(missing))
