import json
import re


def parse_response(response: str) -> dict:
    """
    Extract JSON object from the LLM response.

    The LLM may return:
    - Plain JSON
    - JSON inside ```json ```
    - JSON with additional text

    This function extracts the JSON and converts it
    into a Python dictionary.
    """

    try:

        # Remove markdown code blocks
        cleaned = response.replace(
            "```json", ""
        )

        cleaned = cleaned.replace(
            "```", ""
        )

        cleaned = cleaned.strip()

        # Find JSON object using regex
        match = re.search(
            r"\{.*\}",
            cleaned,
            re.DOTALL
        )

        if not match:

            raise ValueError(
                "No JSON found in LLM response."
            )

        json_string = match.group(0)

        return json.loads(
            json_string
        )

    except Exception as ex:

        raise Exception(
            f"Unable to parse LLM response.\n{ex}"
        )
