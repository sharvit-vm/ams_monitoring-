from categorization_layer.llm.groq_client import groq_client
from categorization_layer.llm.prompt_loader import load_prompt


def generate_ai_explanation(result):

    prompt = load_prompt(
        "explanation_prompt.txt"
    )

    for key, value in result.items():

        prompt = prompt.replace(
            "{" + key + "}",
            str(value)
        )

    return groq_client.chat(prompt)
