import axios from 'axios';

export type WorkflowNode = {
  id: string;
  label: string;
  service: string;
  endpoint: string;
  configured_url?: boolean;
  instance_url?: string;
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
};

export type WorkflowResponse = {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  supported_sources: string[];
  source_platforms?: SourcePlatform[];
};

export type LatestExecutionResponse = {
  status: 'ok' | 'empty';
  message?: string;
  execution?: {
    id: string;
    recorded_at: string;
    response: Record<string, unknown>;
  };
};

const api = axios.create({
  baseURL: import.meta.env.VITE_GATEWAY_URL || '',
});

export async function getWorkflow() {
  const response = await api.get<WorkflowResponse>('/dashboard/workflow');
  return response.data;
}

export async function getLatestExecution() {
  const response = await api.get<LatestExecutionResponse>('/dashboard/executions/latest');
  return response.data;
}
