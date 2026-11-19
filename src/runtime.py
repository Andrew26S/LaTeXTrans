import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from src.configuration import DEFAULT_CONFIG_PATH, DEFAULT_LOCAL_CONFIG_PATH, load_runtime_config

from src.utils.paths import create_unique_dir
from src.utils.progress import log_message
from src.agents.coordinator_agent import CoordinatorAgent
from src.formats.latex.utils import (
    extract_archive,
    prepare_arxiv_source,
    extract_arxiv_ids,
    extract_compressed_files,
    get_arxiv_category,
    get_profect_dirs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ProjectEventCallback = Callable[[Dict[str, Any]], None]


def resolve_path(path_value: str) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def is_local_archive(path: str) -> bool:
    p = Path(path)
    if not path or not p.is_file():
        return False
    lower = p.name.lower()
    return lower.endswith((".zip", ".tar", ".tar.gz", ".tgz"))


def archive_project_dir(archive_path: str, projects_dir: str) -> str:
    name = os.path.basename(archive_path)
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        stem = name[:-7]
    elif lower.endswith(".tgz"):
        stem = name[:-4]
    elif lower.endswith(".tar"):
        stem = name[:-4]
    elif lower.endswith(".zip"):
        stem = name[:-4]
    else:
        stem = os.path.splitext(name)[0]
    return os.path.join(projects_dir, stem)


def extract_local_archive(archive_path: str, projects_dir: str) -> str:
    target_dir = create_unique_dir(Path(archive_project_dir(archive_path, projects_dir)))
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
                projects.append(extract_local_archive(path, projects_dir))
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
) -> Dict[str, Any]:
    completed_projects: List[Dict[str, Any]] = []
    failed_projects: List[Dict[str, Any]] = []
    total_projects = len(projects) + len(preparation_failures)
    for idx, failure in enumerate(preparation_failures, start=1):
        entry = {**failure, "index": idx, "total": total_projects}
        failed_projects.append(entry)
        if event_callback:
            event_callback({"type": "project_error", **entry})

    sources = resolve_path(config.get("tex_sources_dir", "tex source"))
    paper_by_path = {str(sources / paper): paper for paper in extract_arxiv_ids(config.get("paper_list", []))}
    for idx, project_dir in enumerate(projects, start=len(preparation_failures) + 1):
        project_name = os.path.basename(project_dir)
        log_message(f"[{idx}/{total_projects}] Processing {project_name}")
        project = {
            "index": idx, "total": total_projects, "project_name": project_name, "project_dir": project_dir,
        }
        paper = paper_by_path.get(project_dir)
        if paper:
            project["arxiv_id"] = paper
            if paper in config.get("category", {}):
                project["categories"] = config["category"][paper]
        if event_callback:
            event_callback({"type": "project_start", **project})
        coordinator = None
        try:
            coordinator = CoordinatorAgent(config={**config, "arxiv_id": paper}, project_dir=project_dir, output_dir=output_dir)
            project["pdf_path"] = coordinator.workflow_latextrans()
        except Exception as e:
            log_message(f"Error processing project {project_name}: {e}")
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
        if event_callback:
            event_callback({"type": event_type, **project})
    pdfs = []
    for project in completed_projects:
        translated = Path(project["pdf_path"])
        name = project["project_name"]
        original = translated.parent / name / f"{name}.pdf"
        pdfs.extend(str(path) for path in (translated, original) if path.is_file())
    return {
        "pdfs": pdfs,
        "completed_projects": completed_projects,
        "failed_projects": failed_projects,
    }


def build_retry_request(
    failures: Sequence[Dict[str, Any]],
    *,
    config_path: str = DEFAULT_CONFIG_PATH,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build arguments for run_translation using only this batch's failed inputs."""
    retry_overrides = dict(overrides or {})
    retry_overrides["paper_list"] = [item["arxiv_id"] for item in failures if "arxiv_id" in item]
    retry_overrides["replace_paper_list"] = True
    categories = dict(retry_overrides.get("category") or {})
    categories.update({item["arxiv_id"]: item["categories"] for item in failures if "categories" in item})
    if categories:
        retry_overrides["category"] = categories
    return {
        "config_path": config_path,
        "overrides": retry_overrides,
        "project_items": [item["project_dir"] for item in failures if "arxiv_id" not in item],
        "all_existing": False,
    }


def run_translation(
    config_path: str = DEFAULT_CONFIG_PATH,
    overrides: Optional[Dict[str, Any]] = None,
    project_items: Optional[Iterable[str]] = None,
    all_existing: bool = False,
    event_callback: Optional[ProjectEventCallback] = None,
) -> Dict[str, Any]:
    config = load_runtime_config(config_path=config_path, overrides=overrides)
    projects, preparation_failures, projects_dir, output_dir = prepare_projects(
        config=config,
        project_items=project_items,
        all_existing=all_existing,
    )
    project_status = run_projects(
        config=config,
        projects=projects,
        output_dir=output_dir,
        event_callback=event_callback,
        preparation_failures=preparation_failures,
    )
    return {
        "config": config,
        "projects": projects,
        "projects_dir": projects_dir,
        "output_dir": output_dir,
        **project_status,
    }
