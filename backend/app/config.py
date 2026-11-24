"""Reads every setting from environment variables (12-factor style).

Nothing here is hardcoded: API keys, the database path, CORS origins and the
failure-simulation switch all come from the environment / .env file.
"""
import os

from dotenv import load_dotenv

# Load backend/.env (or the repo-root .env) when running without Docker.
# In Docker / on Railway the variables are already in the environment.
load_dotenv()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings:
    def __init__(self) -> None:
        # Key pool: "keyA,keyB" -> ids key_1, key_2 in the same order.
        self.api_keys: list[str] = _split_csv(os.getenv("LLM_API_KEYS", ""))
        self.llm_model: str = os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite")
        # Optional custom base URL (used by the test suite's fake LLM server).
        self.llm_api_base: str | None = os.getenv("LLM_API_BASE") or None
        self.llm_timeout: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
        self.allowed_origins: list[str] = _split_csv(
            os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
        )
        self.enable_failure_simulation: bool = (
            os.getenv("ENABLE_FAILURE_SIMULATION", "false").strip().lower() == "true"
        )
        self.default_system_prompt: str = os.getenv(
            "DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant."
        )

    @property
    def key_ids(self) -> list[str]:
        return [f"key_{i}" for i in range(1, len(self.api_keys) + 1)]

    @property
    def database_path(self) -> str:
        """sqlite:////data/app.db -> /data/app.db ; sqlite:///./app.db -> ./app.db"""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must start with sqlite:///")
        return self.database_url[len(prefix):]


settings = Settings()
