import type { WorkflowResponse } from './api';

type AnyRecord = Record<string, any>;

export function asRecord(value: unknown): AnyRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as AnyRecord : {};
}

export function deriveExecution(raw: unknown) {
  const response = asRecord(raw);
  const categorisation = asRecord(response.categorisation);
  const normalised = asRecord(response.normalised_event);
  const l2 = asRecord(response.l2_rca);
  const l2Response = asRecord(l2.response);
  const l2Data = asRecord(l2Response.data);
  const rca = asRecord(l2Data.rca);
  const fixAgent = asRecord(response.fix_agent);
  const fixAgentExecution = asRecord(fixAgent.execution_result);
  const l3Rca = asRecord(response.l3_rca || response.l3_rca_result || response.rca_result);
  const codefix = asRecord(response.codefix || response.code_fix || response.codefix_result || response.code_fix_result);
  const dbExecution = Object.keys(fixAgentExecution).length ? fixAgentExecution : Object.keys(fixAgent).length ? fixAgent : asRecord(l2Data.execution || response.db_fix || response.db_execution);
  const dbMetrics = asRecord(dbExecution.metrics);
  const dbVerification = asRecord(dbExecution.verification);
  const dbExplanation = asRecord(dbExecution.explanation);
  const l2Metrics = asRecord(l2Response.metrics);

  return {
    response,
    categorisation,
    normalised,
    l2,
    l2Response,
    rca,
    fixAgent,
    fixAgentExecution,
    l3Rca,
    codefix,
    dbExecution,
    dbMetrics,
    dbVerification,
    dbExplanation,
    incident: {
      id: normalised.incident_id || normalised.external_id || categorisation.ticket_id || response.event_id,
      application: normalised.configuration_item || rca.application || 'Unknown',
      technology: categorisation.technology || rca.technology || 'Unknown',
      priority: categorisation.priority || normalised.priority || 'Unknown',
      status: dbExecution.overall_status || codefix.status || fixAgent.status || response.status || 'Unknown',
      confidence: l3Rca.confidence || l3Rca.confidence_score || l2Response.confidence || categorisation.confidence,
      problemDomain: rca.problem_domain || 'Unknown',
      assignedAgent: fixAgent.agent_type || rca.recommended_agent || categorisation.selected_agent || 'Unknown',
      businessImpact: categorisation.business_impact || 'Unknown',
    },
    overallObservability: collectOverallMetrics(response),
    llmObservability: collectLlmMetrics(response),
    rcaReport: buildRcaReport(response),
  };
}

export function toLogLines(raw: unknown): string[] {
  const data = deriveExecution(raw);
  const lines: [string, unknown][] = [
    ['Connector', data.response.source_event_id],
    ['Normalizer', data.response.event_id],
    ['Categorization', data.categorisation.support_level],
    ['L2 RCA', data.l2.status || data.l2Response.status],
    ['DB Fix', data.dbExecution.status],
    ['Verification', data.dbVerification.status],
  ];
  return lines
    .filter(([, value]) => value)
    .map(([label, value]) => `${new Date().toLocaleTimeString()} ${label}: ${String(value)}`);
}

export function chartRows(metrics: AnyRecord) {
  const durations = asRecord(metrics.stage_durations_ms);
  return Object.entries(durations)
    .filter(([, value]) => typeof value === 'number')
    .map(([name, value]) => ({ name: name.replace(/_/g, ' '), ms: value as number }));
}

export type ObservabilityMetric = {
  label: string;
  value: unknown;
  source: string;
};

export function collectOverallMetrics(raw: unknown): ObservabilityMetric[] {
  const response = asRecord(raw);
  const l2 = asRecord(response.l2_rca);
  const l2Response = asRecord(l2.response);
  const l2Execution = asRecord(l2.execution);
  const fixAgent = asRecord(response.fix_agent);
  const fixAgentExecution = asRecord(fixAgent.execution_result);
  const dbExecution = Object.keys(fixAgentExecution).length ? fixAgentExecution : Object.keys(fixAgent).length ? fixAgent : asRecord(asRecord(l2Response.data).execution);
  const dbMetrics = asRecord(dbExecution.metrics);
  const dbVerification = asRecord(dbExecution.verification);
  const stages = asRecord(dbMetrics.stage_durations_ms);

  const metrics: ObservabilityMetric[] = [];
  addMetric(metrics, 'Gateway status', response.status, 'Application gateway');
  addMetric(metrics, 'Pipeline response time', l2Response.execution?.duration_ms || stages.total, 'Application workflow');
  addMetric(metrics, 'L2 API latency', l2Execution.duration_ms, 'Internal API call');
  addMetric(metrics, 'Database health latency', stages.health_read, 'Database');
  addMetric(metrics, 'ServiceNow notification latency', stages.sn_notification, 'External API');
  addMetric(metrics, 'Database active connections', dbVerification.active_connections, 'Database');
  addMetric(metrics, 'Database CPU usage', dbVerification.cpu_usage, 'Database');
  addMetric(metrics, 'Database memory usage', dbVerification.memory_usage, 'Database');
  addMetric(metrics, 'Slow queries', dbVerification.slow_queries, 'Database');
  addMetric(metrics, 'Deadlocks', dbVerification.deadlocks, 'Database');
  addMetric(metrics, 'Actions succeeded', dbMetrics.actions_succeeded, 'Remediation agent');
  addMetric(metrics, 'Actions failed', dbMetrics.actions_failed, 'Remediation agent');
  addMetric(metrics, 'Verification passed', dbMetrics.verification_passed, 'Remediation agent');
  addMetric(metrics, 'External calls', countExternalCalls(response), 'Application workflow');

  Object.entries(stages).forEach(([name, value]) => {
    addMetric(metrics, `${name.replace(/_/g, ' ')} duration`, value, stageSource(name));
  });

  return dedupeMetrics(metrics);
}

export function collectLlmMetrics(raw: unknown): ObservabilityMetric[] {
  const response = asRecord(raw);
  const l2Response = asRecord(asRecord(response.l2_rca).response);
  const l2Metrics = asRecord(l2Response.metrics);
  const tokenUsage = asRecord(l2Metrics.token_usage);
  const metrics: ObservabilityMetric[] = [];

  addMetric(metrics, 'LLM requests', Object.keys(l2Metrics).length ? 1 : undefined, 'L2 RCA agent');
  addMetric(metrics, 'Model name', l2Metrics.model || l2Response.model, 'LLM provider');
  addMetric(metrics, 'LLM latency', l2Metrics.llm_latency_ms, 'L2 RCA agent');
  addMetric(metrics, 'Input tokens', tokenUsage.input_tokens || tokenUsage.prompt_tokens, 'LLM provider');
  addMetric(metrics, 'Output tokens', tokenUsage.output_tokens || tokenUsage.completion_tokens, 'LLM provider');
  addMetric(metrics, 'Total tokens', tokenUsage.total_tokens, 'LLM provider');
  addMetric(metrics, 'Estimated cost', l2Metrics.estimated_cost || l2Metrics.cost_usd, 'LLM provider');
  addMetric(metrics, 'Confidence', l2Metrics.confidence || l2Response.confidence, 'L2 RCA agent');

  return metrics.filter((m) => m.value !== undefined && m.value !== null && m.value !== '');
}

export function buildRcaReport(raw: unknown) {
  const data = deriveExecutionParts(raw);
  const actions = Array.isArray(data.dbExecution.actions) ? data.dbExecution.actions : [];
  const recommendations = [
    data.rca.recommendation,
    data.dbExplanation.summary,
    data.dbExplanation.narrative,
  ].filter(Boolean);

  return {
    incidentSummary: {
      id: data.normalised.incident_id || data.normalised.external_id || data.categorisation.ticket_id,
      title: data.normalised.short_description || data.normalised.message || data.rca.title,
      priority: data.categorisation.priority || data.normalised.priority,
      application: data.normalised.configuration_item || data.rca.application,
      technology: data.categorisation.technology || data.rca.technology,
    },
    executionTimeline: buildTimeline(data),
    identifiedRootCause: data.rca.root_cause || data.dbExecution.root_cause || data.dbExplanation.summary,
    affectedComponents: [data.rca.application, data.rca.target_resource?.name, data.dbExecution.database].filter(Boolean),
    remediationStepsPerformed: actions,
    finalResolutionStatus: data.dbExecution.overall_status || data.dbExecution.status || data.l2.status,
    recommendations,
    agentFindings: {
      categorisation: data.categorisation,
      l2Rca: data.rca,
      remediation: data.dbExecution,
    },
  };
}

function buildTimeline(data: ReturnType<typeof deriveExecutionParts>) {
  const lines: [string, unknown][] = [
    ['Connector', data.response.source_event_id],
    ['Normalizer', data.response.event_id],
    ['Categorization', data.categorisation.support_level],
    ['L2 RCA', data.l2.status || data.l2Response.status],
    ['DB Fix', data.dbExecution.status],
    ['Resolution', data.dbExecution.overall_status],
  ];
  return lines
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}: ${String(value)}`);
}

function deriveExecutionParts(raw: unknown) {
  const response = asRecord(raw);
  const categorisation = asRecord(response.categorisation);
  const normalised = asRecord(response.normalised_event);
  const l2 = asRecord(response.l2_rca);
  const l2Response = asRecord(l2.response);
  const l2Data = asRecord(l2Response.data);
  const rca = asRecord(l2Data.rca);
  const fixAgent = asRecord(response.fix_agent);
  const fixAgentExecution = asRecord(fixAgent.execution_result);
  const l3Rca = asRecord(response.l3_rca || response.l3_rca_result || response.rca_result);
  const codefix = asRecord(response.codefix || response.code_fix || response.codefix_result || response.code_fix_result);
  const dbExecution = Object.keys(fixAgentExecution).length ? fixAgentExecution : Object.keys(fixAgent).length ? fixAgent : asRecord(l2Data.execution || response.db_fix || response.db_execution);
  const dbExplanation = asRecord(dbExecution.explanation);
  return { response, categorisation, normalised, l2, l2Response, rca, dbExecution, dbExplanation };
}

function addMetric(metrics: ObservabilityMetric[], label: string, value: unknown, source: string) {
  if (value !== undefined && value !== null && value !== '') {
    metrics.push({ label, value, source });
  }
}

function dedupeMetrics(metrics: ObservabilityMetric[]) {
  const seen = new Set<string>();
  return metrics.filter((m) => {
    const key = `${m.label}:${m.source}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function stageSource(name: string) {
  if (name.includes('postgres') || name.includes('health') || name.includes('database')) return 'Database';
  if (name.includes('sn') || name.includes('notification')) return 'External API';
  if (name.includes('cmdb')) return 'CMDB service';
  return 'Application workflow';
}

function countExternalCalls(response: AnyRecord) {
  let count = 0;
  if (response.l2_rca) count += 1;
  const dbExecution = asRecord(asRecord(asRecord(asRecord(response.l2_rca).response).data).execution);
  if (dbExecution.sn_notification) count += 1;
  return count;
}

// Keep WorkflowResponse import used for exported API typing.
export type { WorkflowResponse };
