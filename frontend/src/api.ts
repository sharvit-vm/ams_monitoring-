import axios from 'axios';

export type WorkflowNode = {
  id: string;
  label: string;
  service: string;
  endpoint: string;
  configured_url?: boolean;
  instance_url?: string;
};

export type RemediationPlan = {
  approval_id: string;
  agent_type: string;
  target_type: string;
  status: string;
  source_platform: string;
  issue_id: string;
  issue_summary: string;
  severity: string;
  confidence: number;
  recommended_action: string;
  expected_impact: string;
  estimated_execution_time: string;
  risk_level: string;
  evidence: Array<Record<string, unknown>>;
  plan: Array<Record<string, unknown>>;
  execution_context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  approved_by?: string | null;
  approval_reason?: string | null;
  rejected_by?: string | null;
  rejection_reason?: string | null;
  execution_result?: Record<string, unknown> | null;
};

export type WorkflowEdge = {
  source: string;
  target: string;
  condition?: string;
};

export type SourcePlatform = {
  id: string;
  label: string;
  source: string;
  configured_url?: boolean;
  instance_url?: string;
  project_url?: string;
  repo_url?: string;
  create_url?: string;
};

export type WorkflowResponse = {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  supported_sources: string[];
  source_platforms?: SourcePlatform[];
};

export type PendingApprovalsResponse = {
  status: 'ok';
  approvals: RemediationPlan[];
};

export type LatestExecutionResponse = {
  status: 'ok' | 'empty';
  message?: string;
  summary?: {
    total_steps?: number | null;
    completed?: number | null;
    overall_confidence?: number | null;
    workflow_time_ms?: number | null;
  } | null;
  execution?: {
    id: string;
    recorded_at: string;
    response: Record<string, unknown>;
  };
};

export const API_BASE_URL = import.meta.env.VITE_GATEWAY_URL || '';

const api = axios.create({
  baseURL: API_BASE_URL,
});

export async function getWorkflow() {
  const response = await api.get<WorkflowResponse>('/dashboard/workflow');
  return response.data;
}

export async function getLatestExecution() {
  const response = await api.get<LatestExecutionResponse>('/dashboard/executions/latest');
  return response.data;
}

export async function getPendingApprovals() {
  const response = await api.get<PendingApprovalsResponse>('/dashboard/approvals/pending');
  return response.data;
}

export async function getRemediationPlan(approvalId: string) {
  const response = await api.get<{ status: string; remediation_plan: RemediationPlan }>(`/api/v1/remediations/${approvalId}`);
  return response.data.remediation_plan;
}

export async function approveRemediation(approvalId: string, approver: string, reason: string) {
  const response = await api.post(`/api/v1/remediations/${approvalId}/approve`, { approver, reason });
  return response.data;
}

export async function rejectRemediation(approvalId: string, approver: string, reason: string) {
  const response = await api.post(`/api/v1/remediations/${approvalId}/reject`, { approver, reason });
  return response.data;
}
