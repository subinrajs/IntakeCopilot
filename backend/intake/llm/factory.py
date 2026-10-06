from intake.llm.client import LLMClient
from intake.settings import get_settings


def make_llm(backend: str | None = None) -> LLMClient:
    choice = backend or get_settings().llm_backend
    if choice == "dev-oracle":
        from intake.llm.oracle_client import OracleClient

        return OracleClient()
    from intake.llm.openai_client import OpenAIClient

    return OpenAIClient()


def is_simulation(model: str | None) -> bool:
    return model == "dev-oracle"
