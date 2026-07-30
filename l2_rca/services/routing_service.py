class RoutingService:

    def determine_target(self, diagnosis):

        routing = {

            "Database": {
                "agent": "db_fix_agent",
                "resource_type": "Database"
            },

            "Configuration": {
                "agent": "config_fix_agent",
                "resource_type": "Configuration"
            },

            "Infrastructure": {
                "agent": "infrastructure_fix_agent",
                "resource_type": "Infrastructure"
            },

            "Middleware": {
                "agent": "middleware_fix_agent",
                "resource_type": "Middleware"
            },

            "Deployment": {
                "agent": "deployment_fix_agent",
                "resource_type": "Deployment"
            },

            "Network": {
                "agent": "network_fix_agent",
                "resource_type": "Network"
            },

            "Security": {
                "agent": "security_fix_agent",
                "resource_type": "Security"
            }

        }

        return routing.get(
            diagnosis.problem_domain,
            {
                "agent": "manual_review",
                "resource_type": "Unknown"
            }
        )