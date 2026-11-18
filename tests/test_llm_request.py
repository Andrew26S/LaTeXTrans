import asyncio
import json
import unittest
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
        self.assertEqual(result, "Hello.")
        self.assertEqual(agent.fail_caption_phs, ["<PLACEHOLDER_CAP_1>"])

    def test_translate_success_is_sanitized(self):
        agent = make_agent()
        session = FakeSession(FakeResponse(body=ok_body("```latex\n你好。\n\\end{document}\n```")))
        result = run(agent._request_llm_for_trans("sys", "Hello.", "1", "sec", session))
        self.assertEqual(result, "你好。")

    def test_extract_terms_failure_returns_na(self):
        agent = make_agent()
        session = FakeSession(FakeResponse(status=403, raw="forbidden"))
        self.assertEqual(run(agent._request_llm_for_extract_terms("sys", "a", "b", session)), "N/A")


if __name__ == "__main__":
    unittest.main()
