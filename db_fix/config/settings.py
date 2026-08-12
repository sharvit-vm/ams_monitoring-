from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env", override=False)


class Settings(BaseSettings):
    # Neon PostgreSQL — Enterprise Operations Database
    DATABASE_URL: str

    # ServiceNow — incident notification only (no CMDB queries)
    SN_INSTANCE: str = ""
    SN_USERNAME: str = ""
    SN_PASSWORD: str = ""

    class Config:
        env_file = ROOT_DIR / ".env"
        extra = "ignore"


settings = Settings()
