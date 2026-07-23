from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROMPT_FOLDER = PACKAGE_ROOT / "prompts"


def load_prompt(filename: str):
    path = PROMPT_FOLDER / filename
    with open(path, encoding="utf-8") as file:
        return file.read()
