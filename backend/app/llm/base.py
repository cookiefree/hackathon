from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """Base class for provider failures that the API maps to readable HTTP errors."""


class LLMUnavailable(LLMError):
    """The backend can't be reached (e.g. llama-server is not running)."""


class LLMContextExceeded(LLMError):
    """The prompt does not fit into the model's context window."""


class LLMClient(ABC):
    name: str

    @abstractmethod
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """Return a dict that conforms to the JSON schema."""
