def evaluate(payload):

    text = (

        payload.get(
            "title",
            ""
        )

        +

        payload.get(
            "description",
            ""
        )

    ).lower()

    if "production" in text:

        return "HIGH"

    if "uat" in text:

        return "MEDIUM"

    if "dev" in text:

        return "LOW"

    return "UNKNOWN"
