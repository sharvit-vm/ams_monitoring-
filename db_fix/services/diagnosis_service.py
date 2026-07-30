class DiagnosisService:

    def analyze(self, health):

        issues = []

        if health["active_connections"] >= health["max_connections"]:
            issues.append(
                {
                    "issue": "CONNECTION_POOL_EXHAUSTED",
                    "severity": "CRITICAL"
                }
            )

        if health["cpu_usage"] >= 90:
            issues.append(
                {
                    "issue": "HIGH_CPU",
                    "severity": "HIGH"
                }
            )

        if health["memory_usage"] >= 90:
            issues.append(
                {
                    "issue": "HIGH_MEMORY",
                    "severity": "HIGH"
                }
            )

        if health["slow_queries"] > 10:
            issues.append(
                {
                    "issue": "SLOW_QUERIES",
                    "severity": "MEDIUM"
                }
            )

        if health["deadlocks"] > 0:
            issues.append(
                {
                    "issue": "DEADLOCKS",
                    "severity": "HIGH"
                }
            )

        return issues