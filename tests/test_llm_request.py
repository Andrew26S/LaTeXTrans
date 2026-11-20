import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

from src.agents.tool_agents.translator_agent import LLMRequestError, TranslatorAgent


def ok_body(content="译文", finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self._raw = raw if raw is not None else json.dumps(body if body is not None else {})

    async def text(self):
        return self._raw

    async def json(self, content_type="application/json"):
        return json.loads(self._raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Returns (or raises) the queued outcomes in order, one per post() call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.payloads = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_agent(**llm_overrides):
    llm_config = {"model": "m", "base_url": "http://x", "api_key": "k", "timeout": 5}
    llm_config.update(llm_overrides)
    agent = TranslatorAgent(config={"llm_config": llm_config, "category": {}})
    agent.retry_base_delay = 0
    return agent


def run(coro):
    return asyncio.run(coro)


class TranslationSessionTests(unittest.TestCase):
    def test_model_requests_use_environment_proxy_settings_in_each_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            netrc = root / "netrc"
            netrc.write_text("machine provider.invalid login other password dummy\n")
            for name in ("captions", "envs", "inputs", "newcommands"):
                (root / f"{name}_map.json").write_text("[]")
            for mode in (0, 1, 2):
                with self.subTest(mode=mode):
                    (root / "sections_map.json").write_text(json.dumps([
                        {"section": "1", "content": "Hello.", "trans_content": "Old translation"},
                    ]))
                    agent = TranslatorAgent(
                        config={"source_language": "en", "target_language": "ch",
                                "llm_config": {"base_url": "https://provider.invalid/chat", "api_key": "configured"}},
                        trans_mode=mode, project_dir=directory, output_dir=directory,
                        errors_report=[{"part": "sec", "num_or_ph": "1"}],
                    )
                    env = {"NETRC": str(netrc), "https_proxy": "http://user:pass@proxy.invalid:8080",
                           "no_proxy": "provider.invalid" if mode == 2 else ""}
                    with (
                        mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(aiohttp.TCPConnector, "connect", side_effect=RuntimeError("request captured")) as connect,
                        self.assertRaisesRegex(RuntimeError, "request captured"),
                    ):
                        run(agent.execute())
                    request = connect.await_args.args[0]
                    self.assertEqual(request.headers["Authorization"], "Bearer configured")
                    if mode == 2:
                        self.assertIsNone(request.proxy)
                    else:
                        self.assertEqual(request.proxy.host, "proxy.invalid")
                        self.assertEqual(request.proxy_auth, aiohttp.BasicAuth("user", "pass"))


class PostChatTests(unittest.TestCase):
    def test_success(self):
        session = FakeSession(FakeResponse(body=ok_body("  你好  ")))
        self.assertEqual(run(make_agent()._post_chat({}, session)), "你好")

    def test_retries_on_network_error_and_timeout(self):
        session = FakeSession(
            aiohttp.ClientConnectionError("boom"),
            asyncio.TimeoutError(),
            FakeResponse(body=ok_body()),
        )
        self.assertEqual(run(make_agent()._post_chat({}, session)), "译文")
        self.assertEqual(session.outcomes, [])

    def test_retries_on_429_and_5xx(self):
        session = FakeSession(
            FakeResponse(status=429, raw="rate limited", headers={"Retry-After": "0"}),
            FakeResponse(status=503, raw="unavailable"),
            FakeResponse(body=ok_body()),
        )
        self.assertEqual(run(make_agent()._post_chat({}, session)), "译文")

    def test_retries_on_malformed_or_empty_response(self):
        session = FakeSession(
            FakeResponse(body={"error": {"message": "overloaded"}}),  # no "choices"
            FakeResponse(body={"choices": [{"message": {"content": None}}]}),
            FakeResponse(raw="<html>not json</html>"),
        )
        with self.assertRaises(LLMRequestError) as ctx:
            run(make_agent()._post_chat({}, session))
        self.assertIn("malformed response", str(ctx.exception))

    def test_client_error_fails_immediately(self):
        session = FakeSession(FakeResponse(status=401, raw="invalid api key"), FakeResponse(body=ok_body()))
        with self.assertRaises(LLMRequestError) as ctx:
            run(make_agent()._post_chat({}, session))
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(len(session.outcomes), 1)  # did not retry

    def test_gives_up_after_max_attempts(self):
        session = FakeSession(*[aiohttp.ClientConnectionError("down") for _ in range(3)])
        with self.assertRaises(LLMRequestError):
            run(make_agent()._post_chat({}, session))

    def test_retry_delay(self):
        agent = make_agent()
        agent.retry_base_delay = 5
        self.assertEqual(agent._retry_delay(1), 5)
        self.assertEqual(agent._retry_delay(2), 10)
        self.assertEqual(agent._retry_delay(1, "12"), 12)
        self.assertEqual(agent._retry_delay(1, "9999"), 60)
        self.assertEqual(agent._retry_delay(2, "Wed, 21 Oct 2015 07:28:00 GMT"), 10)


class PayloadTests(unittest.TestCase):
    def test_max_tokens_only_sent_when_configured(self):
        self.assertNotIn("max_tokens", make_agent()._build_payload([]))
        self.assertNotIn("max_new_tokens", make_agent()._build_payload([]))
        self.assertEqual(make_agent(max_tokens=4096)._build_payload([])["max_tokens"], 4096)


class SourceReferenceRequestTests(unittest.TestCase):
    def test_translation_glossary_and_repair_include_same_reference(self):
        agent = make_agent()
        agent.source_contexts = {"1": "Paper title: Example"}
        session = FakeSession(*[FakeResponse(body=ok_body()) for _ in range(3)])
        run(agent._request_llm_for_trans("sys", "Source.", "1", "sec", session))
        run(agent._request_llm_for_trans_with_terms("sys", "Source.", "1", "sec", session))
        part = {"content": "Source.", "trans_content": "Old translation"}
        run(agent._request_llm_for_retrans_error_parts("sys", part, "fix", "1", "sec", session))
        for payload in session.payloads:
            reference, target = payload["messages"][1]["content"].split("[Target fragment]")
            self.assertIn("Paper title: Example", reference)
            self.assertIn("Source.", target)
        self.assertIn("Old translation", target)
        self.assertIn("fix", target)


class RequestFallbackTests(unittest.TestCase):
    def test_retranslate_network_error_keeps_previous_translation(self):
        # Previously aiohttp errors escaped this path (it caught requests' exceptions) and crashed the run.
        agent = make_agent()
        session = FakeSession(*[aiohttp.ClientConnectionError("down") for _ in range(3)])
        part = {"content": "Hello.", "trans_content": "你好。"}
        result = run(agent._request_llm_for_retrans_error_parts("sys", part, "err", "1", "sec", session))
        self.assertEqual(result, "你好。")
        self.assertEqual(agent.fail_section_nums, ["1"])
        self.assertTrue(agent.have_fail_parts)

    def test_translate_failure_returns_source_and_marks_part(self):
        agent = make_agent()
        session = FakeSession(FakeResponse(status=400, raw="bad request"))
        result = run(agent._request_llm_for_trans("sys", "Hello.", "<PLACEHOLDER_CAP_1>", "cap", session))
        self.assertEqual(result, ("Hello.", False))
        self.assertEqual(agent.fail_caption_phs, ["<PLACEHOLDER_CAP_1>"])

    def test_translate_success_is_sanitized(self):
        agent = make_agent()
        session = FakeSession(FakeResponse(body=ok_body("```latex\n你好。\n\\end{document}\n```")))
        result = run(agent._request_llm_for_trans("sys", "Hello.", "1", "sec", session))
        self.assertEqual(result, ("你好。", True))

    def test_failed_parts_only_extract_terms_after_successful_retry(self):
        for kind, fields in (
            ("section", {"section": "1"}), ("caption", {"placeholder": "caption"}),
            ("env", {"placeholder": "env", "need_trans": True}),
        ):
            with self.subTest(kind=kind):
                agent = make_agent()
                agent.trans_mode = 2
                agent.update_term = True
                initial_terms = {"GPU": "GPU"} if kind == "env" else {}
                agent.term_dict = dict(initial_terms)
                translate = getattr(agent, f"_translate_{kind}")
                part = {"content": "GPU", **fields}
                session = FakeSession(FakeResponse(status=400, raw="failed"))
                self.assertEqual(run(translate(part, session))["trans_content"], "GPU")
                self.assertTrue(agent.have_fail_parts)
                self.assertEqual(len(session.payloads), 1)
                self.assertEqual(agent.term_dict, initial_terms)
                # An unchanged translation can still be a successful translation.
                session = FakeSession(FakeResponse(body=ok_body("GPU")), FakeResponse(body=ok_body('"GPU" - "GPU"')))
                self.assertEqual(run(translate(part, session))["trans_content"], "GPU")
                self.assertEqual(len(session.payloads), 2)
                self.assertEqual(agent.term_dict["GPU"], "GPU")

    def test_extract_terms_failure_returns_na(self):
        agent = make_agent()
        session = FakeSession(FakeResponse(status=403, raw="forbidden"))
        self.assertEqual(run(agent._request_llm_for_extract_terms("sys", "a", "b", session)), "N/A")


if __name__ == "__main__":
    unittest.main()
