from app import config
from app.llm.base import LLMClient


def get_llm() -> LLMClient:
    """LLM_PROVIDER=stub|llamacpp (default stub, so nothing costs money by accident).

    claude and ollama are not built yet. llamacpp talks to a local llama-server
    (LLAMACPP_URL, LLAMACPP_TIMEOUT) and is created lazily: it does not connect here.
    """
    provider = config.get("LLM_PROVIDER", "stub")
    if provider == "stub":
        from app.llm.stub import StubLLM
        return StubLLM()
    if provider == "llamacpp":
        from app.llm.llamacpp import LlamaCppLLM
        return LlamaCppLLM()
    raise NotImplementedError(f"LLM_PROVIDER={provider!r} is not built yet")
