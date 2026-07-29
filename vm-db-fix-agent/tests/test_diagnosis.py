from services.database_health_service import DatabaseHealthService
from services.diagnosis_service import DiagnosisService

health_service = DatabaseHealthService()
diagnosis_service = DiagnosisService()

health = health_service.get_database_health(
    "insurance_portal"
)

issues = diagnosis_service.analyze(health)

print(issues)