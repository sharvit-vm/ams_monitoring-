TECHNOLOGIES = {

    "guidewire": "Guidewire",

    "policycenter": "Guidewire",

    "claimcenter": "Guidewire",

    "billingcenter": "Guidewire",

    "oracle": "Database",

    "postgres": "Database",

    "mysql": "Database",

    "cpu": "Infrastructure",

    "memory": "Infrastructure",

    "server": "Infrastructure",

    "kubernetes": "Platform",

    "docker": "Platform",

    "aws": "Cloud",

    "azure": "Cloud",

    "gcp": "Cloud",

    "java": "Application",

    "python": "Application",

    "servicenow": "ServiceNow"
}


def detect(payload):

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

    matched = []

    for keyword in TECHNOLOGIES:

        if keyword in text:

            matched.append(

                TECHNOLOGIES[
                    keyword
                ]
            )

    if matched:

        return matched[0], matched

    return "General", []
