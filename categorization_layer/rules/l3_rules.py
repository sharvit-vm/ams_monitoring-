L3_KEYWORDS = [

    "exception",

    "stacktrace",

    "nullpointer",

    "segmentation fault",

    "java",

    "python",

    "traceback",

    "syntax",

    "deployment bug",

    "regression",

    "code defect",

    "compilation"
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

    for keyword in L3_KEYWORDS:

        if keyword in text:

            score += 1

            matched.append(
                keyword
            )

    return score, matched
