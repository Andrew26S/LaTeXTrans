import argparse

from src.utils.progress import log_message
from src.runtime import DEFAULT_LOCAL_CONFIG_PATH, run_translation, split_cli_items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=str, default=DEFAULT_LOCAL_CONFIG_PATH,
        help="Path to the config TOML file (default: %(default)s).",
    )
    parser.add_argument("--model", type=str, default="", help="Model for translating.")
    parser.add_argument("--url", type=str, default="", help="Model url.")
    parser.add_argument("--key", type=str, default="", help="Model key.")
    parser.add_argument("--arxiv", nargs="+", default=[], help="arXiv ID(s), comma-separated.")
    parser.add_argument(
        "--project",
        nargs="+",
        default=[],
        help="Local project path(s) or archive path(s), comma-separated.",
    )
    parser.add_argument("--output", type=str, default="", help="output directory.")
    parser.add_argument("--source", type=str, default="", help="tex source directory.")
    parser.add_argument(
        "--all-existing",
        action="store_true",
        help="Process all existing projects under tex source directory when no --arxiv/--project is provided.",
    )

    args = parser.parse_args()
    result = run_translation(
        config_path=args.config,
        overrides={
            "model": args.model,
            "url": args.url,
            "key": args.key,
            "source": args.source,
            "output": args.output,
            "paper_list": split_cli_items(args.arxiv),
        },
        project_items=split_cli_items(args.project),
        all_existing=args.all_existing,
    )
    failed = len(result["failed_projects"])
    log_message(f"Completed {len(result['completed_projects'])} project(s), failed {failed}.")
    for project in result["failed_projects"]:
        if project.get("compilation_status") == "pdf_with_errors":
            log_message(f"PDFs with compilation errors for {project['project_name']}:")
            for attempt in project["compile_attempts"]:
                if attempt["status"] == "pdf_with_errors":
                    log_message(f"  {attempt['engine']}: {attempt['pdf_path']} (log: {attempt['log_path']})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
