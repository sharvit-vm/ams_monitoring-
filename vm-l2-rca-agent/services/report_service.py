from models.diagnosis import Diagnosis
from models.rca_report import RCAReport, TargetResource
from services.routing_service import RoutingService


class ReportService:

    def __init__(self):
        self.routing_service = RoutingService()

    def generate_report(
        self,
        ticket_id: str,
        application: str,
        technology: str,
        diagnosis: Diagnosis
    ) -> RCAReport:

        # Determine routing
        routing = self.routing_service.determine_target(diagnosis)

        # Create target resource
        target_resource = TargetResource(
            resource_type=routing["resource_type"],
            application=application
        )

        # Generate RCA Report
        return RCAReport(
            ticket_id=ticket_id,
            application=application,
            technology=technology,
            problem_domain=diagnosis.problem_domain,
            recommended_agent=routing["agent"],
            confidence=diagnosis.confidence,
            reason=diagnosis.reason,
            recommended_checks=diagnosis.recommended_checks,
            target_resource=target_resource,
            status="READY_FOR_EXECUTION"
        )