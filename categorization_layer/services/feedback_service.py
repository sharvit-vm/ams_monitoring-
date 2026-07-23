feedback = []


def save_feedback(

        ticket,

        expected,

        predicted

):

    feedback.append(

        {

            "ticket":ticket,

            "expected":expected,

            "predicted":predicted

        }

    )


def get_feedback():

    return feedback
