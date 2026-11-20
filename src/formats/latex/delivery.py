"""Decide how to deliver a translated PDF while keeping the engine result intact."""
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

DeliveryStatus = Literal["ready", "ready_with_warnings", "preview", "failed"]
DELIVERY_LABELS = {
    "ready": "PDF ready",
    "ready_with_warnings": "PDF ready with compilation warnings",
    "preview": "PDF preview needs review",
    "failed": "Compilation failed",
}


def delivery_status(attempt: dict) -> DeliveryStatus:
    """Also support attempts recorded before delivery status was introduced."""
    if "delivery_status" in attempt:
        return attempt["delivery_status"]
    return {"success": "ready", "pdf_with_errors": "preview"}.get(attempt["status"], "failed")


_ERROR = re.compile(r"^(?:![ \t]*|.*?:\d+:[ \t]*|(?=(?:LaTeX|Package \S+|Class \S+) Error:))(.+)", re.M)
_INCOMPLETE = re.compile(
    r"Emergency stop|Fatal error|Runaway argument|No pages of output|Missing character:|"
    r"(?:^No file |Missing input file|(?:LaTeX|Package \S+) Warning: File )[^\n]*"
    r"\.(?:tex|ltx|bib|bbl|sty|cls|pdf|png|jpe?g|eps)\b|^xdvipdfmx:(?:fatal|error):|"
    r"^xdvipdfmx:warning:.*(?:failed|could not|not found)",
    re.M | re.I,
)
_OUTPUT = re.compile(r'^Output written on "?(.+?\.(?:xdv|pdf))"?\s*\((\d+) pages?[,)]', re.M)


def pdf_page_count(pdf: Path) -> int | None:
    """A positive page count means a PDF reader opened the page tree; None means unverified."""
    for command, pattern in ((["pdfinfo"], rb"^Pages:\s*(\d+)"), (["extractbb", "-O"], rb"^%%Pages:\s*(\d+)")):
        try:
            result = subprocess.run([*command, str(pdf)], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        pages = re.search(pattern, result.stdout, re.M)
        if result.returncode or not pages or b"Syntax Error" in result.stderr:
            return 0
        return int(pages[1])
    return None


def _log_text(log: Path) -> str:
    # TeX Live wraps diagnostics even in the middle of a word or file name.
    # Keep the start of an independent error on its own line.
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "".join(line if len(line) == 79 and i + 1 < len(lines) and not _ERROR.match(lines[i + 1])
                   else line + "\n" for i, line in enumerate(lines))


def assess_pdf(pdf: Path | None, log: Path, engine: str, exit_code: int | None) -> tuple[DeliveryStatus, list[str]]:
    if pdf is None or not pdf.is_file() or not pdf.stat().st_size:
        return "failed", ["The build produced no PDF."]
    pages = pdf_page_count(pdf)
    if pages == 0:
        return "failed", ["The reported PDF could not be opened or contains no pages."]
    if pages is None:
        return "preview", ["PDF page count could not be verified (pdfinfo or extractbb is required)."]

    try:
        text = _log_text(log)
    except OSError:
        return "preview", ["The compilation log could not be read."]
    last_pass = re.split(r"^This is (?:XeTeX|LuaHBTeX|LuaTeX|pdfTeX),", text, flags=re.M)[-1]
    incomplete = _INCOMPLETE.search(last_pass)
    if incomplete:
        return "preview", [incomplete[0].strip()]
    if exit_code == 0:
        return "ready", []
    outputs = _OUTPUT.findall(last_pass)
    if not outputs or Path(outputs[-1][0]).stem != pdf.stem or int(outputs[-1][1]) != pages:
        return "preview", ["The final TeX page count could not be matched to this PDF."]

    # Accept only an ordinary latexmk build error attributable to this engine.
    # A failed bibliography, converter or other rule requires review as well.
    summary = text.partition("Collected error summary (may duplicate other messages):")[2]
    rules = [line.strip() for line in summary.splitlines() if line.strip() and not line.lstrip().startswith("Refer to ")]
    if exit_code != 12 or rules != [f"{engine}: Command for '{engine}' gave return code 1"]:
        return "preview", ["The build reported an unverified compiler or auxiliary-tool failure."]

    notes = []
    for error in _ERROR.finditer(text):
        message = error[1]
        block = text[error.end():error.end() + 1500].split("\n\n", 1)[0]
        if (engine == "xelatex" and message.startswith("Package microtype Error: Disabling ligatures of a font")
                and r"Ignoring \DisableLigatures" in block):
            note = "microtype skipped the requested font ligature setting."
        elif (message.startswith("Package babel Error: Unknown option 'latin'.")
              and "latin.ldf" in block and "locale ini file" in block
              and re.search(r"[/\\]babel-[\w-]+[/\\](?!latin\.ldf)[\w-]+\.ldf", last_pass)):
            note = "Babel could not load latin.ldf; another document language was loaded."
        else:
            return "preview", [message]
        if note not in notes:
            notes.append(note)
    if not notes:
        return "preview", ["The compiler error could not be identified."]
    for kind, key in re.findall(r"(Citation|Reference) [`']([^']+)' on page \S+ undefined", last_pass):
        note = f"Unresolved {kind.lower()}: {key}"
        if note not in notes:
            notes.append(note)
    return "ready_with_warnings", notes


def write_compile_report(directory: Path, attempt: dict) -> None:
    fields = ("engine", "exit_code", "status", "delivery_status", "diagnostics", "log_path")
    report = {key: attempt[key] for key in fields if key in attempt}
    (directory / "compilation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
