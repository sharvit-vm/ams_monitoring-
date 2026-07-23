L1_KEYWORDS = [

    "vpn",

    "browser",

    "cache",

    "login",

    "password",

    "authentication",

    "access denied",

    "single user",

    "email issue",

    "outlook"
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

    for keyword in L1_KEYWORDS:

        if keyword in text:

            score += 1

            matched.append(
                keyword
            )

    return score, matched
