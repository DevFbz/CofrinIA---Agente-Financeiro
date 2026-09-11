import pytest

from app.integrations.hermes import HermesInterpreter


@pytest.mark.asyncio
async def test_hermes_interpreter_sends_recent_conversation_context(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "intent": "help",
                "confidence": 0.96,
                "requires_confirmation": False,
                "reply": "Posso ajudar.",
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return Response()

    monkeypatch.setattr("app.integrations.hermes.httpx.AsyncClient", lambda **kwargs: Client())
    interpreter = HermesInterpreter("http://hermes", "test-token")

    result = await interpreter.interpret(
        "5511999999999",
        "E isso?",
        history=[
            {"role": "user", "content": "Gastei R$ 30 no almoço"},
            {"role": "assistant", "content": "Despesa registrada"},
        ],
    )

    assert result["intent"] == "help"
    assert captured["url"] == "http://hermes/cofrinia/interpret"
    assert captured["json"]["history"] == [
        {"role": "user", "content": "Gastei R$ 30 no almoço"},
        {"role": "assistant", "content": "Despesa registrada"},
    ]


@pytest.mark.asyncio
async def test_hermes_interpreter_limits_context_to_last_twelve_turns(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"intent": "unknown", "confidence": 0.9, "requires_confirmation": False}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **kwargs):
            captured["json"] = kwargs["json"]
            return Response()

    monkeypatch.setattr("app.integrations.hermes.httpx.AsyncClient", lambda **kwargs: Client())
    history = [{"role": "user", "content": str(index)} for index in range(20)]

    await HermesInterpreter("http://hermes", "test-token").interpret(
        "5511999999999", "última mensagem", history=history
    )

    assert [item["content"] for item in captured["json"]["history"]] == [str(index) for index in range(8, 20)]
