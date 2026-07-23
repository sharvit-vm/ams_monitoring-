REJECT_KEYWORDS = [

    "password reset",

    "reset password",

    "vacation",

    "leave request",

    "how to",

    "help",

    "install chrome",

    "new account",

    "unlock account",

    "general query"
]


def is_rejected(text):

    text = text.lower()

    for keyword in REJECT_KEYWORDS:

        if keyword in text:

            return True

    return False
