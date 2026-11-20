from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import streamlit as streamlit_backend

from src.formats.latex.delivery import DELIVERY_LABELS, delivery_status
from src.configuration import DEFAULT_LOCAL_CONFIG_PATH, load_runtime_config
from src.runtime import recompile_translation, run_translation, split_multivalue_text
from src.utils.translation_output import pdf_revision
from src.utils.progress import use_reporter


class StreamlitReporter:
    def __init__(self, log_placeholder, stage_bar, stage_text, logs):
        self.log_placeholder = log_placeholder
        self.stage_bar = stage_bar
        self.stage_text = stage_text
        self.logs = logs

    def log(self, message: str) -> None:
        self.logs.extend(message.splitlines())
        self.log_placeholder.code("\n".join(self.logs[-300:]), language="text")

    def progress(self, percent: int, message: str) -> None:
        self.stage_bar.progress(percent)
        self.stage_text.text(message)


def _ensure_session_state() -> None:
    streamlit_backend.session_state.setdefault("job_history", [])
    streamlit_backend.session_state.setdefault("retry_payload", None)
    streamlit_backend.session_state.setdefault("pending_translations", {})
    # Streamlit keeps existing session entries across application reloads.
    for item in streamlit_backend.session_state.job_history:
        for project in item["completed_projects"]:
            if "revision" not in project and project.get("pdf_path"):
                project["revision"] = pdf_revision(Path(project["pdf_path"]))
        inputs = item["inputs"]
        if isinstance(inputs, dict):
            item["inputs"] = inputs["paper_list"] + inputs["project_items"]


def _inject_style() -> None:
    streamlit_backend.set_page_config(
        page_title="LaTeXTrans Studio",
        page_icon="L",
        layout="wide",
    )
    streamlit_backend.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(245, 173, 92, 0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(26, 96, 107, 0.18), transparent 24%),
                linear-gradient(180deg, #f7f2ea 0%, #f1ede4 100%);
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }
        .app-shell {
            padding: 1.25rem 1.5rem;
            border-radius: 24px;
            background: rgba(255, 252, 247, 0.84);
            border: 1px solid rgba(46, 56, 64, 0.08);
            box-shadow: 0 18px 60px rgba(67, 51, 32, 0.10);
            backdrop-filter: blur(12px);
        }
        .hero-title {
            font-size: 2.2rem;
            font-weight: 700;
            line-height: 1.05;
            color: #17323b;
            margin-bottom: 0.35rem;
        }
        .hero-subtitle {
            color: #5f5c53;
            font-size: 1rem;
            margin-bottom: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _sidebar_form():
    sidebar = streamlit_backend.sidebar
    sidebar.header("Run Configuration")
    path = sidebar.text_input("Config Path", DEFAULT_LOCAL_CONFIG_PATH).strip()
    try:
        config = load_runtime_config(path)
    except (OSError, ValueError) as exc:
        sidebar.error(f"Could not load configuration: {exc}")
        return None

    overrides = {}

    def text_option(label, key, default, **widget_options):
        value = sidebar.text_input(label, value=default, key=f"{path}:{key}", **widget_options)
        if value != default:
            overrides[key] = value.strip()

    llm = config["llm_config"]
    text_option("Source Language", "source_language", config["source_language"])
    text_option("Target Language", "target_language", config["target_language"])
    text_option("Model", "model", llm.get("model", "gpt-4o"))
    text_option("Base URL", "url", llm.get("base_url", ""))
    text_option("API Key", "key", llm.get("api_key", ""), type="password")
    text_option("TeX Source Dir", "source", config.get("tex_sources_dir", "tex source"))
    text_option("Output Dir", "output", config.get("output_dir", "outputs"))

    modes = ["0 - Normal", "1 - Retry Errors", "2 - Alt"]
    mode = sidebar.selectbox("Mode", range(len(modes)), index=config.get("mode", 0),
                             format_func=modes.__getitem__, key=f"{path}:mode")
    if mode != config.get("mode", 0):
        overrides["mode"] = mode
    update_term = sidebar.checkbox("Update Terms", value=config["update_term"], key=f"{path}:update_term")
    if update_term != config["update_term"]:
        overrides["update_term"] = update_term
    text_option("User Terms", "user_term", config.get("user_term", ""), help="Path to a terminology CSV file.")
    all_existing = sidebar.checkbox("Process All Existing Projects", value=False)
    return {"config_path": path, "overrides": overrides, "all_existing": all_existing}


def _collect_inputs() -> Dict[str, List[str]]:
    left, right = streamlit_backend.columns([1.15, 0.85], gap="large")
    with left:
        arxiv_text = streamlit_backend.text_area(
            "arXiv IDs",
            value="",
            height=120,
            placeholder="2508.18791v2, 2407.01648",
            help="Use commas or new lines. Versioned IDs are supported.",
        )
    with right:
        project_text = streamlit_backend.text_area(
            "Local Projects or Archives",
            value="",
            height=120,
            placeholder=r"D:\path\paper.tar.gz",
            help="Supports extracted folders and .zip/.tar/.tar.gz/.tgz archives.",
        )

    return {
        "paper_list": split_multivalue_text(arxiv_text),
        "project_items": split_multivalue_text(project_text),
    }


def _append_history(result, request, logs):
    history_item = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "retry_request": result["retry_request"],
        "inputs": list(result["config"].get("paper_list", [])) + request["project_items"],
        "output_dir": result["output_dir"],
        "completed_projects": result["completed_projects"],
        "failed_projects": result["failed_projects"],
        "preview_projects": result.get("preview_projects", []),
        "pdfs": result["pdfs"],
        "logs": list(logs[-80:]),
    }
    streamlit_backend.session_state.job_history.insert(0, history_item)
    streamlit_backend.session_state.job_history = streamlit_backend.session_state.job_history[:12]


def _queue_retry(retry, title):
    streamlit_backend.session_state.retry_payload = {"request": retry, "title": title}


def _resolve_existing(key, replace):
    project = streamlit_backend.session_state.pending_translations.pop(key)
    if replace:
        _queue_retry(project["replace_request"], f"Replace translation of {project['project_name']}")


def _render_pending() -> None:
    for key, project in streamlit_backend.session_state.pending_translations.items():
        source = project.get("input_archive", project["project_dir"])
        streamlit_backend.warning(f"Translating {source} would replace the existing PDF at {project['pdf_path']}.")
        cancel, replace = streamlit_backend.columns(2)
        cancel.button("Cancel translation", key=f"cancel-{key}", on_click=_resolve_existing, args=(key, False))
        replace.button("Replace translation", key=f"replace-{key}", on_click=_resolve_existing, args=(key, True))


def _show_compile_attempts(projects, *, downloads=False):
    for project in projects:
        attempts = project.get("compile_attempts", [])
        if not attempts:
            continue
        streamlit_backend.text(project["project_name"])
        for note in project.get("diagnostics", []):
            streamlit_backend.write(note)
        streamlit_backend.table(attempts)
        if downloads or project.get("delivery_status") == "preview":
            for attempt in attempts:
                if delivery_status(attempt) == "preview" and attempt.get("pdf_path"):
                    pdf = Path(attempt["pdf_path"])
                    try:
                        streamlit_backend.download_button(
                            f"Download preview for review ({attempt['engine']})", data=pdf.read_bytes(),
                            file_name=pdf.name, mime="application/pdf",
                            key=f"partial-{downloads}-{project['index']}-{pdf}", on_click="ignore",
                        )
                    except OSError:
                        streamlit_backend.warning(f"Could not read {pdf}")


def _render_result_files(result, request) -> None:
    output_dir = Path(result["output_dir"])
    completed = result["completed_projects"]
    failed = result["failed_projects"]
    previews = result.get("preview_projects", [])
    pdfs = [Path(path) for path in result["pdfs"]]

    streamlit_backend.subheader("Results")
    stat_a, stat_b, stat_c = streamlit_backend.columns(3)
    stat_a.metric("Published", str(len(completed)))
    stat_b.metric("Failed", str(len(failed)))
    stat_c.metric("Previews for review", str(len(previews)))

    streamlit_backend.code(str(output_dir), language="text")
    streamlit_backend.caption("Output directory. Use it directly in File Explorer or your terminal.")

    if pdfs:
        with streamlit_backend.expander("Generated PDF Files", expanded=True):
            for idx, pdf_path in enumerate(pdfs, start=1):
                streamlit_backend.write(f"{idx}. `{pdf_path.name}`")
                streamlit_backend.code(str(pdf_path), language="text")
                try:
                    with open(pdf_path, "rb") as f:
                        streamlit_backend.download_button(
                            label=f"Download {pdf_path.name}",
                            data=f.read(),
                            file_name=pdf_path.name,
                            mime="application/pdf",
                            key=f"download_pdf_{idx}_{pdf_path.name}",
                            on_click="ignore",
                        )
                except OSError:
                    streamlit_backend.warning(f"Could not read {pdf_path}")
    else:
        message = "Preview PDFs are available under Compilation details." if previews else "No published PDF."
        streamlit_backend.info(message)

    for project in completed + previews:
        status = project.get("delivery_status")
        if status in {"ready_with_warnings", "preview"}:
            streamlit_backend.warning(f"{project['project_name']}: {DELIVERY_LABELS[status]}")
    projects = completed + previews + failed
    if any(project.get("compile_attempts") for project in projects):
        with streamlit_backend.expander("Compilation details", expanded=bool(failed or previews or any(p.get("diagnostics") for p in completed))):
            _show_compile_attempts(projects, downloads=True)

    if failed:
        for project in failed:
            streamlit_backend.text(f"{project['project_name']}: {project['error']}")
        streamlit_backend.button(
            "Retry Failed Projects", use_container_width=True, on_click=_queue_retry,
            args=(result["retry_request"], f"Retry {len(failed)} failed project(s)"),
        )

    _append_history(result, request, streamlit_backend.session_state.current_run_logs)


def _render_history() -> None:
    history = streamlit_backend.session_state.job_history
    streamlit_backend.subheader("Task History")
    if not history:
        streamlit_backend.caption("No jobs recorded in this session yet.")
        return

    for index, item in enumerate(history):
        label = (
            f"{item['timestamp']} | "
            f"{len(item['completed_projects'])} published / {len(item.get('preview_projects', []))} previews / "
            f"{len(item['failed_projects'])} failed | "
            f"{Path(item['output_dir']).name}"
        )
        with streamlit_backend.expander(label, expanded=index == 0):
            streamlit_backend.write("Inputs")
            streamlit_backend.code("\n".join(item["inputs"]), language="text")

            streamlit_backend.write("Output Directory")
            streamlit_backend.code(item["output_dir"], language="text")

            current = []
            superseded_dirs = set()
            for project in item["completed_projects"]:
                path = Path(project["pdf_path"])
                if "revision" in project and pdf_revision(path) != project["revision"]:
                    streamlit_backend.caption(f"The translation of {project['project_name']} from this run was replaced or removed.")
                    superseded_dirs.add(path.parent)
                else:
                    current.append(project)
            if item["pdfs"]:
                streamlit_backend.write("PDF Files")
                for pdf in item["pdfs"]:
                    if not any(Path(pdf).is_relative_to(folder) for folder in superseded_dirs):
                        streamlit_backend.code(pdf, language="text")

            previews = item.get("preview_projects", [])
            _show_compile_attempts(current + previews + item["failed_projects"])
            for project in item["completed_projects"] + previews + item["failed_projects"]:
                if run := project.get("recovery_run"):
                    _render_recompile(project, Path(run))
            if item["failed_projects"]:
                streamlit_backend.write("Failed Projects")
                streamlit_backend.code("\n".join(p["project_name"] for p in item["failed_projects"]), language="text")
                if item.get("retry_request"):
                    streamlit_backend.button(
                        "Retry Failed Projects from This Run",
                        key=f"retry_history_{index}", use_container_width=True, on_click=_queue_retry,
                        args=(item["retry_request"], f"Retry failed projects from {item['timestamp']}"),
                    )
                else:
                    streamlit_backend.caption("Start a new translation to select a provider for this older run.")

            streamlit_backend.write("Recent Logs")
            streamlit_backend.code("\n".join(item["logs"]), language="text")


def _render_recompile(project: dict, run: Path) -> None:
    streamlit_backend.text(f"Saved translated project: {run / 'project'}")
    if streamlit_backend.button("Recompile saved project", key=f"recompile-{run}"):
        try:
            with streamlit_backend.spinner("Compiling edited LaTeX project…"):
                project["recompile_attempt"] = recompile_translation(run)
        except (ValueError, OSError, RuntimeError) as exc:
            streamlit_backend.error(f"Could not recompile: {exc}")
            return
    attempt = project.get("recompile_attempt")
    if attempt:
        streamlit_backend.text(f"Recompile: {DELIVERY_LABELS[delivery_status(attempt)]} (log: {attempt['log_path']})")
        for note in attempt.get("diagnostics", []):
            streamlit_backend.write(note)
        if attempt["pdf_path"] and delivery_status(attempt) != "failed":
            pdf = Path(attempt["pdf_path"])
            try:
                streamlit_backend.download_button(
                    "Download recompiled PDF", data=pdf.read_bytes(), file_name=pdf.name,
                    mime="application/pdf", key=f"recompiled-{run}", on_click="ignore",
                )
            except OSError:
                streamlit_backend.warning(f"Could not read {pdf}")


def _run_streamlit_job(request: Dict[str, Any], title: str) -> None:
    streamlit_backend.subheader(title)
    status_col, stats_col = streamlit_backend.columns([1.6, 1], gap="large")
    with status_col:
        project_text = streamlit_backend.empty()
        stage_text = streamlit_backend.empty()
        overall_bar = streamlit_backend.progress(0.0)
        stage_bar = streamlit_backend.progress(0.0)
    with stats_col:
        stats_placeholder = streamlit_backend.empty()

    log_placeholder = streamlit_backend.empty()
    results_placeholder = streamlit_backend.empty()

    logs = []
    streamlit_backend.session_state.current_run_logs = logs
    reporter = StreamlitReporter(log_placeholder, stage_bar, stage_text, logs)

    def on_event(event: Dict[str, Any]) -> None:
        index, total = event["index"], event["total"]
        stats_placeholder.metric("Projects", f"{index}/{total}")
        project_text.markdown(f"**Project** `{index}/{total}`  `{event['project_name']}`")
        if event["type"] == "project_start":
            overall_bar.progress((index - 1) / total)
            reporter.progress(0, "Starting project...")
        else:
            overall_bar.progress(index / total)
            if event["type"] == "project_error":
                stage_text.text(f"Error in {event['project_name']}: {event['error']}")

    try:
        with use_reporter(reporter):
            result = run_translation(**request, event_callback=on_event)
    except Exception as exc:
        reporter.log(f"Run failed: {exc}")
        stage_text.markdown(f"**Stage** Failed: {exc}")
        results_placeholder.error(f"Run failed: {exc}")
        return

    pending = result.get("pending_projects", [])
    for project in pending:
        key = (project["pdf_path"], project.get("input_archive", project["project_dir"]))
        streamlit_backend.session_state.pending_translations[key] = project
    if pending and not (result["completed_projects"] or result["failed_projects"] or result.get("preview_projects")):
        stage_text.text("Waiting for a replacement decision.")
        return

    stage_bar.progress(1.0)
    stage_text.markdown("**Stage** Finished")
    completed = len(result["completed_projects"])
    failed = len(result["failed_projects"])
    previews = len(result.get("preview_projects", []))
    warnings = sum(p.get("delivery_status") == "ready_with_warnings" for p in result["completed_projects"])
    summary = f"Published {completed} ({warnings} with warnings), previews {previews}, failed {failed}."
    if pending:
        summary += f" Awaiting confirmation for {len(pending)}."
    if not (failed or previews or warnings):
        results_placeholder.success(summary)
    elif completed or previews:
        results_placeholder.warning(summary)
    else:
        results_placeholder.error(summary)
    _render_result_files(result, request)


def main() -> None:
    _ensure_session_state()
    _inject_style()
    streamlit_backend.markdown(
        """
        <div class="app-shell">
            <div class="hero-title">LaTeXTrans Studio</div>
            <p class="hero-subtitle">
                Run arXiv or local LaTeX translation jobs with live workflow progress, logs, configurable runtime parameters, and session-level job history.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    request = _sidebar_form()
    inputs = _collect_inputs()

    streamlit_backend.caption(
        "Provide arXiv IDs, local projects, or enable all-existing mode. Results, failed jobs, and recent runs stay visible in this session."
    )

    retry_payload = streamlit_backend.session_state.pop("retry_payload", None)
    if retry_payload:
        if "llm_config" in retry_payload.get("request", {}).get("overrides", {}):
            _run_streamlit_job(**retry_payload)
        else:
            streamlit_backend.warning("Start a new translation to select a provider for this older run.")
    elif streamlit_backend.button("Start Translation", type="primary", use_container_width=True, disabled=request is None):
        request["overrides"]["paper_list"] = inputs["paper_list"]
        request["project_items"] = inputs["project_items"]
        _run_streamlit_job(request, title="Current Run")

    _render_pending()
    _render_history()


if __name__ == "__main__":
    main()
