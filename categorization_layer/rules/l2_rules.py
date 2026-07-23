L2_KEYWORDS = [

    "cpu",

    "memory",

    "disk",

    "database",

    "server",

    "api",

    "latency",

    "performance",

    "pod",

    "kubernetes",

    "timeout",

    "connection refused",

    "gateway",

    "job failed",

    "application"
]


def match(payload):

    score = 0

    matched = []

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

    for keyword in L2_KEYWORDS:

        if keyword in text:

            score += 1

            matched.append(
                keyword
            )

    return score, matched
