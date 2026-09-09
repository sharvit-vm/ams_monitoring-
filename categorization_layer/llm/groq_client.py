import os

from dotenv import load_dotenv

from groq import Groq
from observability.token_usage import provider_call

load_dotenv()


class GroqClient:

    def __init__(self):

        self.client = None

        self.model = os.getenv(

            "GROQ_MODEL",

            "llama-3.3-70b-versatile"

        )

    def chat(

            self,

            prompt,

            temperature=0,

            max_tokens=400

    ):

        api_key = os.getenv(
            "GROQ_API_KEY"
        )

        if not api_key:

            raise RuntimeError(
                "GROQ_API_KEY is not configured"
            )

        if self.client is None:

            self.client = Groq(

                api_key=api_key

            )

        response = provider_call(self.client.chat.completions.create,

            model=self.model,

            temperature=temperature,

            max_tokens=max_tokens,

            messages=[

                {

                    "role": "user",

                    "content": prompt

                }

            ]

        )

        return response.choices[0].message.content


groq_client = GroqClient()

