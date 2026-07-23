import hashlib


incident_memory = {}


def find_similar(

        payload

):

    text = (

        payload.get("title","")

        +

        payload.get("description","")

    )

    key = hashlib.md5(

        text.encode()

    ).hexdigest()

    if key in incident_memory:

        return incident_memory[key]

    return None


def remember(

        payload,

        result

):

    text = (

        payload.get("title","")

        +

        payload.get("description","")

    )

    key = hashlib.md5(

        text.encode()

    ).hexdigest()

    incident_memory[key] = result
