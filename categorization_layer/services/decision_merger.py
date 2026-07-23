def merge_decision(

        rule_result,

        ai_result

):

    if rule_result["confidence"] >= 0.90:

        return rule_result

    if ai_result["confidence"] > rule_result["confidence"]:

        return {

            "support_level":

                ai_result[
                    "support_level"
                ],

            "technology":

                ai_result[
                    "technology"
                ],

            "business_impact":

                ai_result[
                    "business_impact"
                ],

            "confidence":

                ai_result[
                    "confidence"
                ],

            "source":

                "AI"
        }

    return {

        **rule_result,

        "source":

            "Rules"
    }
