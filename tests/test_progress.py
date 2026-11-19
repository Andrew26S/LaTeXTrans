import asyncio
import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, call

from src.gui.streamlit_app import StreamlitReporter
from src.utils.progress import CliReporter, log_message, report_progress, use_reporter


class WorkflowOutputTests(unittest.TestCase):
    def test_overlapping_async_jobs_and_child_tasks_keep_their_own_output(self):
        async def exercise():
            first_entered = asyncio.Event()
            second_entered = asyncio.Event()
            first_finished = asyncio.Event()
            first, second = Mock(), Mock()

            async def child(name):
                report_progress(1, name)
                log_message(name)

            async def job_one():
                with use_reporter(first):
                    first_entered.set()
                    await second_entered.wait()
                    await asyncio.create_task(child("one"))
                first_finished.set()

            async def job_two():
                await first_entered.wait()
                with use_reporter(second):
                    second_entered.set()
                    await first_finished.wait()
                    await asyncio.create_task(child("two"))

            await asyncio.gather(job_one(), job_two())
            self.assertEqual(first.method_calls, [call.progress(1, "one"), call.log("one")])
            self.assertEqual(second.method_calls, [call.progress(1, "two"), call.log("two")])

        asyncio.run(exercise())

    def test_exception_restores_outer_reporter_and_preserves_standard_streams(self):
        stdout, stderr = sys.stdout, sys.stderr
        outer, inner = Mock(), Mock()
        with use_reporter(outer):
            with self.assertRaises(ValueError), use_reporter(inner):
                self.assertIs(sys.stdout, stdout)
                self.assertIs(sys.stderr, stderr)
                log_message("inner error")
                raise ValueError("failure")
            log_message("outer")
        inner.log.assert_called_once_with("inner error")
        outer.log.assert_called_once_with("outer")
        self.assertIs(sys.stderr, stderr)

    def test_cli_pipe_writes_separate_lines_and_terminal_finishes_before_log(self):
        for tty in (False, True):
            with self.subTest(tty=tty):
                stream = io.StringIO()
                stream.isatty = lambda: tty
                with redirect_stdout(stream), use_reporter(CliReporter()):
                    report_progress(10, "Compiling")
                    log_message("LaTeX error")
                self.assertEqual(stream.getvalue(), ("\r" if tty else "") + "[ 10%] Compiling\nLaTeX error\n")

    def test_gui_updates_stage_from_progress_and_keeps_multiline_diagnostics(self):
        placeholder, bar, text, logs = Mock(), Mock(), Mock(), []
        with use_reporter(StreamlitReporter(placeholder, bar, text, logs)):
            report_progress(1, "Downloading")
            log_message("Error at 99%\nsecond line")
        bar.progress.assert_called_once_with(1)
        text.text.assert_called_once_with("Downloading")
        self.assertEqual(logs, ["Error at 99%", "second line"])
        placeholder.code.assert_called_once_with("Error at 99%\nsecond line", language="text")
