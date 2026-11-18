from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"   # keep in sync with .env.example
    gemini_thinking_budget: int | None = None      # None = auto: 0 for gemini-2.5 models (faster JSON), model default otherwise
    database_url: str = "sqlite:///./breaker.db"
    llm_cache: str = "breaker_llm_cache.jsonl"   # replay identical LLM calls; set LLM_CACHE= (empty) to disable


settings = Settings()