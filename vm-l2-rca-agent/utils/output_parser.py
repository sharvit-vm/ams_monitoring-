import json
import re


class OutputParser:

    @staticmethod
    def parse(response: str) -> dict:
        """
        Cleans LLM response and converts it into JSON.
        """

        # Remove markdown code fences
        cleaned = re.sub(r"```json|```", "", response).strip()

        try:
            return json.loads(cleaned)

        except json.JSONDecodeError as ex:
            raise ValueError(
                f"Unable to parse LLM response.\n\nResponse:\n{response}"
            ) from ex