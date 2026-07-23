def evaluate(payload):

    priority = payload.get(
        "priority"
    )

    mapping = {

        "1": "P1",

        "2": "P2",

        "3": "P3",

        "4": "P4",

        "5": "P5",

        "P1":"P1",

        "P2":"P2",

        "P3":"P3",

        "P4":"P4",

        "P5":"P5"
    }

    return mapping.get(
        priority,
        "UNKNOWN"
    )
