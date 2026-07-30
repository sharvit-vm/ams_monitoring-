from pathlib import Path
from pydantic_settings import BaseSettings

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    APP_NAME: str = "L2 RCA Agent"
    APP_VERSION: str = "1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    GROQ_API_KEY: str
    DB_FIX_AGENT_URL: str = ""

    class Config:
        env_file = ROOT_DIR / ".env"
        extra = "ignore"


settings = Settings()
