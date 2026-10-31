"""Owner 2026-10-31: ``words_per_request`` is a maximum, never awaited - a
shorter unit goes to the LLM at once, a longer one is split in order."""

import json

import httpx

from src.clients.llm import LlmClient
from src.core.runtime import LlmSettings


def llm(words_per_request):
    calls = []

    def handle(req):
        content = json.loads(req.content)["messages"][-1]["content"]
        text = content.split("\n")[0].removeprefix("TEXT: ")  # no NEXT line
        calls.append(text)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    class Target:
        def current(self):
            return "http://w", "t"

        def invalidate(self, kind):
            pass

    client = LlmClient(
        Target(),
        LlmSettings(words_per_request=words_per_request),
        transport=httpx.MockTransport(handle),
    )
    return client, calls


def test_fewer_words_than_the_limit_go_in_one_request():
    client, calls = llm(4)
    assert client.translate("raz dwa trzy", "pl") == "raz dwa trzy"
    assert calls == ["raz dwa trzy"]


def test_ten_words_limit_four_gives_4_4_2():
    client, calls = llm(4)
    client.translate("a1 a2 a3 a4 a5 a6 a7 a8 a9 a10", "pl")
    assert [len(c.split()) for c in calls] == [4, 4, 2]  # last one not held back
