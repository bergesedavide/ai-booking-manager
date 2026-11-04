import httpx


class OllamaError(Exception):
    """Errore durante la comunicazione con Ollama."""


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
        self.timeout = timeout

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
            )
            response.raise_for_status()

            data = response.json()
            content = data["message"]["content"]

            if not isinstance(content, str):
                raise OllamaError("Risposta di Ollama non valida.")

            return content

        except httpx.TimeoutException as exc:
            raise OllamaError("Timeout durante la richiesta a Ollama.") from exc

        except httpx.RequestError as exc:
            raise OllamaError("Impossibile comunicare con Ollama.") from exc

        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"Ollama ha restituito HTTP {exc.response.status_code}."
            ) from exc

        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaError("Formato della risposta di Ollama non valido.") from exc