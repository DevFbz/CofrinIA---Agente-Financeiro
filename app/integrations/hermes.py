from __future__ import annotations

import os
from typing import Any

import httpx


class HermesInterpretationError(RuntimeError):
    pass


class HermesInterpreter:
    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = url or os.getenv("HERMES_API_URL")
        self.token = token or os.getenv("COFRIN_HERMES_TOKEN")

    async def interpret(
        self,
        phone: str,
        message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.url or not self.token:
            raise HermesInterpretationError("ponte Hermes não configurada")
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{self.url.rstrip('/')}/cofrinia/interpret",
                    headers={"X-Cofrin-Hermes-Token": self.token},
                    json={
                        "phone": phone,
                        "message": message,
                        "history": [
                            {"role": item.get("role"), "content": str(item.get("content") or "")[:4000]}
                            for item in (history or [])[-12:]
                            if item.get("role") in {"user", "assistant"}
                        ],
                    },
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HermesInterpretationError("não foi possível consultar o Hermes") from exc
        if not isinstance(data, dict) or not {"intent", "confidence", "requires_confirmation"}.issubset(data):
            raise HermesInterpretationError("resposta Hermes incompleta")
        if not 0 <= float(data["confidence"]) <= 1:
            raise HermesInterpretationError("confiança Hermes inválida")
        return data
