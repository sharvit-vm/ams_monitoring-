from pathlib import Path

from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):

    SN_INSTANCE: str

    SN_USERNAME: str

    SN_PASSWORD: str

    DATABASE_URL: str

    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"


settings = Settings()
