from pydantic import BaseModel


class ExecutionReport(BaseModel):

    ticket_id: str

    application: str

    execution_status: str

    action_taken: str

    verification_status: str

    remarks: str