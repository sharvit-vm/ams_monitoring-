from datetime import datetime, timedelta

from categorization_layer.utils.logger_config import get_logger

logger = get_logger(__name__)

# ------------------------------------------------------------------
# Demo in-memory incident store.
# Replace with Redis / DB / Vector DB later.
# ------------------------------------------------------------------
RECENT_INCIDENTS = []


def check_duplicate(payload):

    title = str(
        payload.get(
            "title",
            ""
        )
    ).lower()

    technology = payload.get(
        "technology",
        ""
    )

    now = datetime.utcnow()

    # Remove incidents older than 10 minutes
    RECENT_INCIDENTS[:] = [

        incident

        for incident in RECENT_INCIDENTS

        if now - incident["created_at"] <= timedelta(minutes=10)
    ]

    for incident in RECENT_INCIDENTS:

        if (

            incident["title"] == title

            and

            incident["technology"] == technology

        ):

            logger.info(
                f"Duplicate detected: {incident['ticket_id']}"
            )

            return {

                "duplicate": True,

                "duplicate_of":
                    incident["ticket_id"],

                "reason":
                    "Matching title and technology within 10 minutes."
            }

    RECENT_INCIDENTS.append({

        "ticket_id":
            payload.get(
                "ticket_id"
            ),

        "title":
            title,

        "technology":
            technology,

        "created_at":
            now
    })

    return {

        "duplicate": False,

        "duplicate_of": None,

        "reason":
            "No duplicate incident found."
    } 
