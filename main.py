import argparse
from pathlib import Path

from src.formats.latex.delivery import DELIVERY_LABELS, delivery_status
from src.utils.progress import log_message
from src.configuration import DEFAULT_LOCAL_CONFIG_PATH
from src.runtime import recompile_translation, run_translation, split_cli_items


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
        "--compile-only", nargs="?", const=".", metavar="RUN_DIR",
        help="Compile the saved project/ in a run directory (default: current directory).",
    )
    parser.add_argument(
        "--all-existing",
        action="store_true",
        help="Process all existing projects under tex source directory when no --arxiv/--project is provided.",
    )

    args = parser.parse_args()
    if args.compile_only is not None:
        if args.project or args.arxiv or args.all_existing or any((args.model, args.url, args.key, args.output, args.source)):
            parser.error("--compile-only takes a saved run directory and cannot be combined with translation options")
        try:
            attempt = recompile_translation(args.compile_only)
        except (ValueError, OSError, RuntimeError) as exc:
            log_message(f"Could not recompile: {exc}")
            return 1
        delivery = delivery_status(attempt)
        log_message(DELIVERY_LABELS[delivery])
        return 0 if delivery in {"ready", "ready_with_warnings"} else 1
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
    completed = result["completed_projects"]
    previews = result.get("preview_projects", [])
    warnings = sum(p.get("delivery_status") == "ready_with_warnings" for p in completed)
    log_message(f"Published {len(completed)} ({warnings} with warnings), previews {len(previews)}, failed {failed}.")
    if result.get("cancelled_projects"):
        log_message(f"Cancelled {len(result['cancelled_projects'])} project(s).")
    for project in completed + previews + result["failed_projects"]:
        status = project.get("delivery_status")
        if status in {"preview", "ready_with_warnings"}:
            log_message(f"{DELIVERY_LABELS[status]}: {project['pdf_path']}")
            for note in project.get("diagnostics", []):
                log_message(f"  {note}")
        if project.get("recovery_run"):
            log_message(f"Edit the saved project, then run latextrans --compile-only from {project['recovery_run']}")
    return 1 if failed or previews else 0


if __name__ == "__main__":
    raise SystemExit(main())
