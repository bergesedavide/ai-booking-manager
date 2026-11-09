from enum import Enum

import httpx


class OllamaErrorKind(str, Enum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    HTTP = "http"
    INVALID_RESPONSE = "invalid_response"


class OllamaError(Exception):
    """Categorized error without request or response contents."""

    def __init__(self, kind: OllamaErrorKind):
        self.kind = kind
        super().__init__(kind.value)


class OllamaClient:
    """Client HTTP per comunicare con Ollama."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:3b",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = httpx.Timeout(timeout, connect=5.0)

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Invia una conversazione a Ollama e restituisce il testo generato."""

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": 256,
            },
        }

        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
                follow_redirects=False,
            )
            response.raise_for_status()

            data = response.json()
            if (not isinstance(data, dict) or data.get("done") is not True
                    or not isinstance(data.get("message"), dict)):
                raise OllamaError(OllamaErrorKind.INVALID_RESPONSE)
            content = data["message"].get("content")
            if not isinstance(content, str) or not content.strip():
                raise OllamaError(OllamaErrorKind.INVALID_RESPONSE)

            return content

        except httpx.TimeoutException:
            raise OllamaError(OllamaErrorKind.TIMEOUT) from None
        except httpx.RequestError:
            raise OllamaError(OllamaErrorKind.CONNECTION) from None
        except httpx.HTTPStatusError:
            raise OllamaError(OllamaErrorKind.HTTP) from None
        except (KeyError, TypeError, ValueError):
            raise OllamaError(OllamaErrorKind.INVALID_RESPONSE) from None
