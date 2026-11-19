import os
from typing import Any, Dict, List, Optional
from pathlib import Path
import sys
import asyncio

base_dir = os.getcwd()
sys.path.append(base_dir)

from src.utils.paths import create_unique_dir
from src.utils.progress import log_message
from .tool_agents.base_tool_agent import BaseToolAgent
from .tool_agents.parser_agent import ParserAgent
from .tool_agents.translator_agent import TranslatorAgent 
from .tool_agents.generator_agent import GeneratorAgent
from .tool_agents.validator_agent import ValidatorAgent
import gc


class CoordinatorAgent:
    """
    The main orchestrator agent for the translation system.
    It coordinates the workflow of various tool agents based on document format
    and configuration.
    """

    def __init__(self, 
                 config: Dict[str, Any],
                 project_dir: str = None,
                 output_dir: Optional[str] = None
                 ):
        """
        Initializes the CoordinatorAgent.
        """
        self.config = config
        self.name = config.get("sys_name", "LaTeXTrans")
        self.target_language = config.get("target_language", "ch")
        self.source_language = config.get("source_language", "en")
        self.project_dir = project_dir  # Project path for parsing
        self.output_dir = output_dir  # Output directory for parsed files
        self.loop = asyncio.new_event_loop()
        self.mode = config.get("mode", 0)
        self.compile_attempts = []

    def run_async(self, coro):
        """
        Run asynchronous coroutines in the existing event loop
        """
        return self.loop.run_until_complete(coro)

    async def workflow_latextrans_async(self) -> str:
        """
        initializes the tool agent based on the provided agent name key.
        """
        base_name = os.path.basename(self.project_dir)
        transed_project_dir = str(create_unique_dir(Path(self.output_dir, f"{self.target_language}_{base_name}")))

        parser_agent = ParserAgent(config=self.config,
                                   project_dir=self.project_dir,
                                   output_dir=transed_project_dir)
        parser_agent.execute()  

        translator_agent = TranslatorAgent(config=self.config,
                                           project_dir=self.project_dir,
                                           output_dir=transed_project_dir,
                                           trans_mode=self.mode)
        await translator_agent.execute()  # await
        validator_agent = ValidatorAgent(config=self.config,
                                            project_dir=self.project_dir,
                                            output_dir=transed_project_dir)
        errors_report = validator_agent.execute()
        MAX_RETRIES = 3
        retry_count = 0
        if errors_report:
            translator_agent.trans_mode = 1

        while errors_report and retry_count < MAX_RETRIES: # 3 times
            translator_agent.errors_report = errors_report
            await translator_agent.execute(error_retry_count=retry_count, Maxtry=MAX_RETRIES)
            errors_report = validator_agent.execute(errors_report)
            retry_count += 1

        generator_agent = GeneratorAgent(config=self.config,
                                         project_dir=self.project_dir,
                                         output_dir=transed_project_dir)
        try:
            generated_pdf = generator_agent.execute()
        finally:
            self.compile_attempts = list(generator_agent.compile_attempts)
        if not generated_pdf:
            if any(a["status"] == "pdf_with_errors" for a in self.compile_attempts):
                raise RuntimeError("PDF produced with compilation errors. See the engine logs.")
            raise RuntimeError("Compilation produced no translated PDF.")
        if not os.path.isfile(generated_pdf) or os.path.getsize(generated_pdf) == 0:
            raise RuntimeError(f"Generated PDF is missing or empty: {generated_pdf}")

        final_pdf = os.path.join(transed_project_dir, f"{self.target_language}_{base_name}.pdf")
        os.replace(generated_pdf, final_pdf)
        if self.compile_attempts:
            self.compile_attempts[-1]["pdf_path"] = final_pdf
        log_message(f"🤖🎉 {self.name}: Successfully translated {base_name} to {final_pdf}.")
        return final_pdf


    def workflow_latextrans(self) -> str:
        """
        Initialize the tool agent and execute the LaTeX conversion workflow 
        (with event loop security management)
        """

        if hasattr(self, 'loop') and not self.loop.is_closed():
            self.loop.close()  

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            return self.loop.run_until_complete(self.workflow_latextrans_async())

        finally:
            # Complete all asynchronous resource recycling
            if tasks := asyncio.all_tasks(self.loop):
                self.loop.run_until_complete(
                    asyncio.gather(*tasks, return_exceptions=True)
                )

            # Special handling of asynchronous I/O recycling in Windows
            if sys.platform == "win32":
                self.loop.run_until_complete(
                    self.loop.shutdown_asyncgens()
                )

            self.loop.run_until_complete(self.loop.shutdown_default_executor())
