import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from src.configuration import DEFAULT_CONFIG_PATH, DEFAULT_LOCAL_CONFIG_PATH, load_runtime_config

from src.utils.paths import create_unique_dir
from src.utils.translation_output import pdf_revision, recompile_directory, translation_slot
from src.utils.progress import log_message
from src.agents.coordinator_agent import CoordinatorAgent, PreviewAvailable
from src.formats.latex.compile import CompileAttempt, LaTexCompiler
from src.formats.latex.delivery import delivery_status, write_compile_report
from src.formats.latex.utils import (
    archive_stem,
    extract_archive,
    prepare_arxiv_source,
    extract_arxiv_ids,
    extract_compressed_files,
    get_arxiv_category,
    get_profect_dirs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ProjectEventCallback = Callable[[Dict[str, Any]], None]


def recompile_translation(run_dir: str | Path) -> CompileAttempt:
    """Compile a retained translation in a fresh build directory."""
    run = Path(run_dir).expanduser().resolve()
    project = run / "project"
    if not project.is_dir():
        raise ValueError(f"No saved translated LaTeX project at {project}. "
                         "Select a run directory containing project/. Older PDF-only runs have no source to recompile.")
    with recompile_directory(run) as output:
        compiler = LaTexCompiler(str(project), str(output))
        compiler.compile()
        if not compiler.attempts:
            raise ValueError(f"No main TeX file found in {project}.")
        attempt = compiler.attempts[-1]
        write_compile_report(output, attempt)
        return attempt


def resolve_path(path_value: str) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return Path(os.path.abspath(p))
    return (PROJECT_ROOT / p).resolve()


def is_local_archive(path: str) -> bool:
    p = Path(path)
    return p.is_file() and bool(archive_stem(p))


def extract_local_archive(archive_path: str, projects_dir: str) -> str:
    stem = archive_stem(Path(archive_path))
    if not stem:
        raise ValueError(f"Unrecognized archive format: {archive_path}")
    target_dir = create_unique_dir(Path(projects_dir, stem))
    try:
        extract_archive(Path(archive_path), target_dir)
    except Exception:
        shutil.rmtree(target_dir)
        raise
    return str(target_dir)


def split_cli_items(values: Sequence[str]) -> List[str]:
    raw = " ".join(values)
    return [item.strip() for item in raw.split(",") if item.strip()]


def split_multivalue_text(value: str) -> List[str]:
    if not value:
        return []
    normalized = value.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def prepare_projects(
    config: Dict[str, Any],
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
    archive_inputs: Optional[Dict[str, str]] = None,
) -> tuple[List[str], List[Dict[str, Any]], str, str]:
    input_items = config.get("paper_list", [])
    project_items = [item for item in (project_items or []) if item]
    if not input_items and not project_items and not all_existing:
        raise ValueError("No input provided. Supply an arXiv ID or local project, configure paper_list, or enable all-existing.")

    projects_dir = str(resolve_path(config.get("tex_sources_dir", "tex source")))
    output_dir = str(resolve_path(config.get("output_dir", "outputs")))

    os.makedirs(projects_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    projects = []
    failures = []

    def record_failure(source, error, *, arxiv=False):
        failure = {"project_name": source if arxiv else Path(source).name, "error": str(error)}
        failure["arxiv_id" if arxiv else "project_dir"] = source
        failures.append(failure)
        log_message(f"Error preparing {source}: {error}")

    paper_list = []
    for item in input_items:
        ids = extract_arxiv_ids([item])
        if ids:
            paper_list.extend(ids)
        else:
            record_failure(item, "Invalid arXiv ID or URL.", arxiv=True)

    categories = config.setdefault("category", {})
    for paper in dict.fromkeys(paper_list):
        try:
            path = prepare_arxiv_source(paper, projects_dir)
            if not config.get("user_term") and paper not in categories:
                categories.update(get_arxiv_category([paper]))
            projects.append(path)
        except Exception as exc:
            record_failure(paper, exc, arxiv=True)

    for item in project_items:
        path = item
        try:
            path = str(resolve_path(item))
            if os.path.isdir(path):
                projects.append(path)
            elif is_local_archive(path):
                if archive_inputs is None:
                    projects.append(extract_local_archive(path, projects_dir))
                else:
                    # The output name comes from the archive. Extraction can
                    # wait until the publication lock and replacement decision.
                    archive_inputs[path] = path
                    projects.append(path)
            else:
                record_failure(path, "Invalid local project path.")
        except Exception as e:
            record_failure(path, e)

    if not input_items and not project_items:
        log_message("No explicit inputs. Processing all existing projects in the specified directory.")
        extraction_errors = extract_compressed_files(projects_dir)
        for archive, reason in extraction_errors.items():
            # Nested archives belong to the containing paper. Its workflow
            # determines whether the missing asset actually prevents translation.
            if Path(archive).parent == Path(projects_dir):
                record_failure(archive, reason)
        projects = get_profect_dirs(projects_dir)
        if not projects and not failures:
            raise ValueError("No projects found. Check 'tex_sources_dir' and 'paper_list' in config.")

    projects = list(dict.fromkeys(os.path.abspath(p) for p in projects))
    return projects, failures, projects_dir, output_dir


def run_projects(
    config: Dict[str, Any],
    projects: Sequence[str],
    output_dir: str,
    event_callback: Optional[ProjectEventCallback] = None,
    preparation_failures: Sequence[Dict[str, Any]] = (),
    archive_inputs: Optional[Dict[str, str]] = None,
    confirm_replace: Optional[Callable[[Path], bool]] = None,
    replace_revisions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    completed_projects: List[Dict[str, Any]] = []
    preview_projects: List[Dict[str, Any]] = []
    failed_projects: List[Dict[str, Any]] = []
    pending_projects = []
    cancelled_projects = []
    prepared_projects = []
    total_projects = len(projects) + len(preparation_failures)
    for idx, failure in enumerate(preparation_failures, start=1):
        entry = {**failure, "index": idx, "total": total_projects}
        failed_projects.append(entry)
        if event_callback:
            event_callback({"type": "project_error", **entry})

    sources = resolve_path(config.get("tex_sources_dir", "tex source"))
    paper_by_path = {str(sources / paper): paper for paper in extract_arxiv_ids(config.get("paper_list", []))}
    for idx, project_dir in enumerate(projects, start=len(preparation_failures) + 1):
        archive = (archive_inputs or {}).get(project_dir)
        if project_dir != archive:
            prepared_projects.append(project_dir)
        project_name = archive_stem(Path(archive)) if archive else os.path.basename(project_dir)
        log_message(f"[{idx}/{total_projects}] Processing {project_name}")
        project = {
            "index": idx, "total": total_projects, "project_name": project_name, "project_dir": project_dir,
        }
        if archive:
            project["input_archive"] = archive
        paper = paper_by_path.get(project_dir)
        if paper:
            project["arxiv_id"] = paper
            if paper in config.get("category", {}):
                project["categories"] = config["category"][paper]
        if event_callback:
            event_callback({"type": "project_start", **project})
        coordinator = None
        try:
            output_name = f"{config.get('target_language', 'ch')}_{project_name}"
            with translation_slot(Path(output_dir), output_name) as destination:
                pdf = destination / f"{output_name}.pdf"
                revision = pdf_revision(pdf)
                if replace_revisions and str(pdf) not in replace_revisions:
                    raise ValueError("The selected output has changed. Start a new translation.")
                if revision and (replace_revisions or {}).get(str(pdf)) != revision:
                    if confirm_replace is None or not confirm_replace(pdf):
                        project.update(pdf_path=str(pdf), revision=revision)
                        deferred = confirm_replace is None
                        (pending_projects if deferred else cancelled_projects).append(project)
                        if event_callback:
                            event_callback({"type": "project_pending" if deferred else "project_cancelled", **project})
                        continue
                if project_dir == archive:
                    project_dir = extract_local_archive(archive, str(sources))
                    project["project_dir"] = project_dir
                    prepared_projects.append(project_dir)
                coordinator = CoordinatorAgent(
                    config={**config, "arxiv_id": paper}, project_dir=project_dir, output_dir=output_dir,
                    destination=destination, output_name=output_name,
                )
                project["pdf_path"] = coordinator.workflow_latextrans()
                project["revision"] = pdf_revision(Path(project["pdf_path"]))
        except PreviewAvailable as e:
            project["message"] = str(e)
            preview_projects.append(project)
            event_type = "project_preview"
        except Exception as e:
            log_message(f"Error processing project {archive or project_name}: {e}")
            project["error"] = str(e)
            failed_projects.append(project)
            event_type = "project_error"
        else:
            completed_projects.append(project)
            event_type = "project_complete"

        attempts = list(coordinator.compile_attempts) if coordinator is not None else []
        if attempts:
            project["compile_attempts"] = attempts
            status = attempts[-1]["status"]
            if status == "failed" and any(a["status"] == "pdf_with_errors" for a in attempts):
                status = "pdf_with_errors"
            project["compilation_status"] = status
            attempt = attempts[-1]
            if event_type == "project_preview":
                attempt = next(a for a in reversed(attempts) if a.get("pdf_path")
                               and delivery_status(a) == "preview")
                project["pdf_path"] = attempt["pdf_path"]
            project["delivery_status"] = {
                "project_preview": "preview", "project_error": "failed",
            }.get(event_type, attempt.get("delivery_status", "ready"))
            project["diagnostics"] = attempt.get("diagnostics", [])
        if coordinator is not None and coordinator.recovery_run:
            project["recovery_run"] = str(coordinator.recovery_run)
        if event_callback:
            event_callback({"type": event_type, **project})
    pdfs = []
    for project in completed_projects:
        translated = Path(project["pdf_path"])
        name = project["project_name"]
        original = translated.parent / name / f"{name}.pdf"
        pdfs.extend(str(path) for path in (translated, original) if path.is_file())
    return {
        "projects": prepared_projects,
        "pdfs": list(dict.fromkeys(pdfs)),
        "completed_projects": completed_projects,
        "preview_projects": preview_projects,
        "failed_projects": failed_projects,
        "pending_projects": pending_projects,
        "cancelled_projects": cancelled_projects,
    }


def build_retry_request(
    failures: Sequence[Dict[str, Any]],
    *,
    config_path: str = DEFAULT_CONFIG_PATH,
    overrides: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build arguments for run_translation using only this batch's failed inputs."""
    retry_overrides = dict(overrides or {})
    if llm_config is not None:
        for key in ("url", "key", "model"):
            retry_overrides.pop(key, None)
        retry_overrides["llm_config"] = dict(llm_config)
    retry_overrides["paper_list"] = [item["arxiv_id"] for item in failures if "arxiv_id" in item]
    retry_overrides["replace_paper_list"] = True
    categories = dict(retry_overrides.get("category") or {})
    categories.update({item["arxiv_id"]: item["categories"] for item in failures if "categories" in item})
    if categories:
        retry_overrides["category"] = categories
    request = {
        "config_path": config_path,
        "overrides": retry_overrides,
        "project_items": [item["project_dir"] for item in failures if "arxiv_id" not in item],
        "all_existing": False,
    }
    prepared_archives = {item["project_dir"]: item["input_archive"] for item in failures
                         if "input_archive" in item and item["input_archive"] != item["project_dir"]}
    if prepared_archives:
        request["prepared_archives"] = prepared_archives
    return request


def run_translation(
    config_path: str = DEFAULT_CONFIG_PATH,
    overrides: Optional[Dict[str, Any]] = None,
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
    event_callback: Optional[ProjectEventCallback] = None,
    confirm_replace: Optional[Callable[[Path], bool]] = None,
    replace_revisions: Optional[Dict[str, str]] = None,
    prepared_archives: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    config = load_runtime_config(config_path=config_path, overrides=overrides)
    archive_inputs = dict(prepared_archives or {})
    projects, preparation_failures, projects_dir, output_dir = prepare_projects(
        config=config,
        project_items=project_items,
        all_existing=all_existing, archive_inputs=archive_inputs,
    )
    project_status = run_projects(
        config=config,
        projects=projects,
        output_dir=output_dir,
        event_callback=event_callback,
        preparation_failures=preparation_failures, archive_inputs=archive_inputs,
        confirm_replace=confirm_replace, replace_revisions=replace_revisions,
    )
    retry_settings = {
        "source": projects_dir, "output": output_dir,
        "source_language": config["source_language"], "target_language": config["target_language"],
        "mode": config.get("mode", 0), "user_term": config.get("user_term", ""),
        "update_term": config["update_term"], "llm_config": dict(config["llm_config"]),
        "category": dict(config.get("category", {})), "replace_category": True,
    }
    for project in project_status["pending_projects"]:
        request = build_retry_request([project], config_path=config_path, overrides=retry_settings)
        request["replace_revisions"] = {project["pdf_path"]: project["revision"]}
        project["replace_request"] = request
    return {
        "config": config,
        "projects_dir": projects_dir,
        "output_dir": output_dir,
        "retry_request": build_retry_request(
            project_status["failed_projects"], config_path=config_path, overrides=retry_settings,
        ) if project_status["failed_projects"] else None,
        **project_status,
    }
