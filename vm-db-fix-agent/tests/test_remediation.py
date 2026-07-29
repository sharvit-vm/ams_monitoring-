from services.database_health_service import DatabaseHealthService
from services.diagnosis_service import DiagnosisService
from services.remediation_service import RemediationService

health = DatabaseHealthService().get_database_health("insurance_portal")

issues = DiagnosisService().analyze(health)

actions = RemediationService().generate_plan(issues)

for action in actions:
    print(action)