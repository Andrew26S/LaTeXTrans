import argparse
from pathlib import Path

from src.utils.progress import log_message
from src.configuration import DEFAULT_LOCAL_CONFIG_PATH
from src.runtime import run_translation, split_cli_items


def confirm_replace(pdf: Path) -> bool:
    try:
        answer = input(f"Translation already exists at {pdf}. Replace it? [y/N] ")
    except EOFError:
        answer = ""
    replace = answer.strip().lower() in {"y", "yes"}
    if not replace:
        log_message(f"Cancelled translation. Kept {pdf}.")
    return replace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=str, default=DEFAULT_LOCAL_CONFIG_PATH,
        help="Path to the config TOML file (default: %(default)s).",
    )
    parser.add_argument("--model", type=str, help="Model for translating.")
    parser.add_argument("--url", type=str, help="Model url.")
    parser.add_argument("--key", type=str, help="Model key.")
    parser.add_argument("--arxiv", nargs="+", default=[], help="arXiv ID(s), comma-separated.")
    parser.add_argument(
        "--project",
        nargs="+",
        default=[],
        help="Local project path(s) or archive path(s), comma-separated.",
    )
    parser.add_argument("--output", type=str, help="output directory.")
    parser.add_argument("--source", type=str, help="tex source directory.")
    parser.add_argument(
        "--all-existing",
        action="store_true",
        help="Process all existing projects under tex source directory when no --arxiv/--project is provided.",
    )

    args = parser.parse_args()
    overrides = {
        key: value for key, value in vars(args).items()
        if key in ("model", "url", "key", "source", "output") and value is not None
    }
    overrides["paper_list"] = split_cli_items(args.arxiv)
    result = run_translation(
        config_path=args.config,
        overrides=overrides,
        project_items=split_cli_items(args.project),
        all_existing=args.all_existing,
        confirm_replace=confirm_replace,
    )
    failed = len(result["failed_projects"])
    log_message(f"Completed {len(result['completed_projects'])} project(s), failed {failed}.")
    if result.get("cancelled_projects"):
        log_message(f"Cancelled {len(result['cancelled_projects'])} project(s).")
    for project in result["failed_projects"]:
        if project.get("compilation_status") == "pdf_with_errors":
            log_message(f"PDFs with compilation errors for {project['project_name']}:")
            for attempt in project["compile_attempts"]:
                if attempt["status"] == "pdf_with_errors":
                    log_message(f"  {attempt['engine']}: {attempt['pdf_path']} (log: {attempt['log_path']})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
