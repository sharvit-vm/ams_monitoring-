from pathlib import Path

from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    APP_NAME: str
    APP_VERSION: str

    HOST: str
    PORT: int

    GROQ_API_KEY: str
    DB_FIX_AGENT_URL: str

    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"


settings = Settings()
