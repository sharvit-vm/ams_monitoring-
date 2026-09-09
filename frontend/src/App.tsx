import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  InputLabel,
  LinearProgress,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import HourglassTopIcon from '@mui/icons-material/HourglassTop';
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined';
import DatabaseOutlinedIcon from '@mui/icons-material/StorageOutlined';
import AccountTreeOutlinedIcon from '@mui/icons-material/AccountTreeOutlined';
import SecurityOutlinedIcon from '@mui/icons-material/SecurityOutlined';
import SettingsSuggestOutlinedIcon from '@mui/icons-material/SettingsSuggestOutlined';
import BugReportOutlinedIcon from '@mui/icons-material/BugReportOutlined';
import CodeOutlinedIcon from '@mui/icons-material/CodeOutlined';
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined';
import PaperPlaneOutlinedIcon from '@mui/icons-material/SendOutlined';
import CheckOutlinedIcon from '@mui/icons-material/CheckOutlined';
import CloseOutlinedIcon from '@mui/icons-material/CloseOutlined';
import RefreshOutlinedIcon from '@mui/icons-material/RefreshOutlined';
import NotificationsNoneOutlinedIcon from '@mui/icons-material/NotificationsNoneOutlined';
import HelpOutlineOutlinedIcon from '@mui/icons-material/HelpOutlineOutlined';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import ExpandMoreOutlinedIcon from '@mui/icons-material/ExpandMoreOutlined';
import HomeOutlinedIcon from '@mui/icons-material/HomeOutlined';
import ListAltOutlinedIcon from '@mui/icons-material/ListAltOutlined';
import HubOutlinedIcon from '@mui/icons-material/HubOutlined';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import BarChartOutlinedIcon from '@mui/icons-material/BarChartOutlined';
import { API_BASE_URL, approveRemediation, getLatestExecution, getPendingApprovals, getWorkflow, rejectRemediation, type RemediationPlan, type SourcePlatform, type WorkflowNode } from './api';
import { asRecord, deriveExecution } from './derive';

type NodeStatus = 'completed' | 'running' | 'pending' | 'skipped' | 'failed';

type DisplayNode = {
  id: string;
  index: number;
  status: NodeStatus;
  title: string;
  subtitle: string;
  icon: ReactNode;
  metrics: string[];
  accent: 'green' | 'blue' | 'purple';
  downstream: boolean;
  approvalId: string;
  approvalStatus?: string;
  l2Status?: NodeStatus;
  l3Status?: NodeStatus;
  l2Metrics?: string[];
  l3Metrics?: string[];
  followupStatus?: NodeStatus;
  routeLevel?: string;
  dbVerification?: Record<string, unknown>;
  dbMetrics?: Record<string, unknown>;
  dbExecution?: Record<string, unknown>;
  fixAgent?: Record<string, unknown>;
  fixAgentExecution?: Record<string, unknown>;
  l3Rca?: Record<string, unknown>;
  codefix?: Record<string, unknown>;
  normalised?: Record<string, unknown>;
  agentType?: string;
};

export function App() {
  const queryClient = useQueryClient();
  const workflow = useQuery({ queryKey: ['workflow'], queryFn: getWorkflow });
  const latest = useQuery({ queryKey: ['latest-execution'], queryFn: getLatestExecution });
  const approvals = useQuery({ queryKey: ['pending-approvals'], queryFn: getPendingApprovals });
  const [selectedSource, setSelectedSource] = useState<'servicenow' | 'jira' | 'github'>('servicenow');
  const [selectedStatus, setSelectedStatus] = useState('all');
  const [selectedDateRange, setSelectedDateRange] = useState('last7');
  const [selectedApprovalId, setSelectedApprovalId] = useState<string>('');
  const [decisionNote, setDecisionNote] = useState('');
  const [approver, setApprover] = useState('approver@company.com');
  const [approvalError, setApprovalError] = useState('');

  const rawResponse = latest.data?.execution?.response ?? {};
  const execution = useMemo(() => deriveExecution(rawResponse), [rawResponse]);
  const latestApproval = approvals.data?.approvals?.[0];
  const selectedApproval = useMemo(() => {
    if (!selectedApprovalId) return undefined;
    return approvals.data?.approvals.find((item) => item.approval_id === selectedApprovalId);
  }, [approvals.data?.approvals, selectedApprovalId]);
  const nodes = workflow.data?.nodes ?? [];
  const sourcePlatforms = workflow.data?.source_platforms ?? [];
  const sourceOptions = sourcePlatforms.length
    ? sourcePlatforms
    : [
        { id: 'servicenow', label: 'ServiceNow', source: 'servicenow' },
        { id: 'jira', label: 'Jira', source: 'jira' },
        { id: 'github', label: 'GitHub', source: 'github' },
      ];
  const executionSourceKey = sourceKey(execution.normalised.source || execution.response.source || execution.response.source_event?.source || selectedSource);
  const selectedSourceKey = hasRecordData(execution.response) ? executionSourceKey : selectedSource;
  const activeNodes = useMemo(
    () => buildDisplayNodes(nodes, execution, selectedSourceKey, latestApproval, selectedApproval),
    [nodes, execution, selectedSourceKey, latestApproval, selectedApproval],
  );
  const selectedPlatform = sourceOptions.find((platform) => sourceKey(platform.source) === selectedSource) ?? sourceOptions[0];
  const sourceActionLabel = selectedSource === 'servicenow' ? 'Create Incident' : 'Create Issue';
  const selectedPlatformUrl = getSourceCreateUrl(selectedPlatform);
  const selectedPlatformHasExecution = hasRecordData(execution.response) && executionSourceKey === selectedSource;
  const updatedAt = latest.data?.execution?.recorded_at;
  const dashboardSummary = latest.data?.summary;
  const totalSteps = dashboardSummary?.total_steps ?? countWorkflowSteps(workflow.data?.nodes ?? []);
  const completedSteps = dashboardSummary?.completed ?? activeNodes.filter((node) => node.status === 'completed').length;
  const overallConfidence = formatPercentSummary(dashboardSummary?.overall_confidence ?? deriveOverallConfidence(execution));
  const workflowTime = formatDurationSummary(dashboardSummary?.workflow_time_ms ?? deriveWorkflowTimeMs(execution));

  useEffect(() => {
    const streamUrl = `${API_BASE_URL.replace(/\/$/, '')}/dashboard/executions/stream`;
    const stream = new EventSource(streamUrl);
    stream.addEventListener('execution', () => {
      queryClient.invalidateQueries({ queryKey: ['latest-execution'] });
      queryClient.invalidateQueries({ queryKey: ['pending-approvals'] });
    });
    return () => stream.close();
  }, [queryClient]);

  const approveMutation = useMutation({
    mutationFn: async () => {
      if (!selectedApproval) throw new Error('No approval selected');
      return approveRemediation(selectedApproval.approval_id, approver, decisionNote);
    },
    onSuccess: async () => {
      setDecisionNote('');
      setSelectedApprovalId('');
      setApprovalError('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['pending-approvals'] }),
        queryClient.invalidateQueries({ queryKey: ['latest-execution'] }),
        queryClient.invalidateQueries({ queryKey: ['workflow'] }),
      ]);
    },
    onError: (error) => {
      setApprovalError(error instanceof Error ? error.message : 'Approval failed');
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async () => {
      if (!selectedApproval) throw new Error('No approval selected');
      return rejectRemediation(selectedApproval.approval_id, approver, decisionNote || 'Rejected by approver');
    },
    onSuccess: async () => {
      setDecisionNote('');
      setSelectedApprovalId('');
      setApprovalError('');
      await queryClient.invalidateQueries({ queryKey: ['pending-approvals'] });
    },
    onError: (error) => {
      setApprovalError(error instanceof Error ? error.message : 'Rejection failed');
    },
  });

  return (
    <Box className="explorer-shell">
      <aside className="left-rail">
        <Box className="rail-brand">
          <Box className="rail-mark">P</Box>
        </Box>
        <nav className="rail-nav">
          <button className="rail-button active" title="Overview"><HomeOutlinedIcon /></button>
          <button className="rail-button" title="Intake"><ListAltOutlinedIcon /></button>
          <button className="rail-button" title="Guardrails"><SecurityOutlinedIcon /></button>
          <button className="rail-button" title="Approvals"><GroupsOutlinedIcon /></button>
          <button className="rail-button" title="Observability"><BarChartOutlinedIcon /></button>
          <button className="rail-button" title="Docs"><DescriptionOutlinedIcon /></button>
          <button className="rail-button" title="Settings"><SettingsOutlinedIcon /></button>
        </nav>
      </aside>

      <Box className="main-shell">
        <header className="explorer-header">
          <Stack direction="row" spacing={1} alignItems="center" className="top-bar">
            <FormControl size="small" className="source-control">
              <InputLabel id="source-select-label">Source</InputLabel>
              <Select
                labelId="source-select-label"
                label="Source"
                value={selectedSource}
                onChange={(event) => setSelectedSource(event.target.value as 'servicenow' | 'jira' | 'github')}
              >
                {sourceOptions.map((platform) => (
                  <MenuItem key={platform.id} value={platform.source}>{platform.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" className="source-control status-control">
              <InputLabel id="status-select-label">Status</InputLabel>
              <Select
                labelId="status-select-label"
                label="Status"
                value={selectedStatus}
                onChange={(event) => setSelectedStatus(event.target.value)}
              >
                <MenuItem value="all">All</MenuItem>
                <MenuItem value="running">In Progress</MenuItem>
                <MenuItem value="pending">Pending</MenuItem>
                <MenuItem value="completed">Completed</MenuItem>
                <MenuItem value="skipped">Skipped</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" className="source-control date-control">
              <InputLabel id="date-range-select-label">Date range</InputLabel>
              <Select
                labelId="date-range-select-label"
                label="Date range"
                value={selectedDateRange}
                onChange={(event) => setSelectedDateRange(event.target.value)}
              >
                <MenuItem value="last24">Last 24 hours</MenuItem>
                <MenuItem value="last7">Last 7 days</MenuItem>
                <MenuItem value="last30">Last 30 days</MenuItem>
              </Select>
            </FormControl>
            <SummaryStat label="Total Steps" value={totalSteps} />
            <SummaryStat label="Completed" value={completedSteps} />
            <SummaryStat label="Overall Confidence" value={overallConfidence} />
            <SummaryStat label="Workflow Time" value={workflowTime} />
            <Stack direction="row" spacing={1} alignItems="center" className="top-right-utilities">
              <IconButton className="utility-icon"><NotificationsNoneOutlinedIcon /></IconButton>
              <IconButton className="utility-icon"><HelpOutlineOutlinedIcon /></IconButton>
              <IconButton className="utility-icon"><SettingsOutlinedIcon /></IconButton>
              <Box className="avatar-pill">Gs</Box>
              <IconButton className="utility-icon"><ExpandMoreOutlinedIcon /></IconButton>
            </Stack>
          </Stack>
        </header>

        <Box className="workflow-tabbar">
          <button className="tab active">Workflow</button>
          <Box className="workflow-actions">
            <Button
              className="ghost-btn"
              startIcon={<RefreshOutlinedIcon />}
              onClick={() => {
                workflow.refetch();
                latest.refetch();
                approvals.refetch();
              }}
            >
              Process overview
            </Button>
            <Box className="header-pill">
              <span>Updated</span>
              <b>{updatedAt ? new Date(updatedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }) : 'N/A'}</b>
            </Box>
          </Box>
        </Box>

        {latest.isFetching && <LinearProgress className="page-progress" />}

        <main className="explorer-canvas">
          {workflow.isError && <Alert severity="warning">Workflow metadata was not returned by the backend.</Alert>}
          {latest.isError && <Alert severity="warning">Latest execution data was not returned by the backend.</Alert>}

          <Box className="workflow-stack">
            {selectedPlatform && (
              <WorkflowCard
                index={1}
                title={selectedPlatform.label}
                subtitle="External incident platform"
                icon={<Box className="sn-badge">{sourceBadge(selectedPlatform.source)}</Box>}
                status={selectedPlatformHasExecution ? 'completed' : 'pending'}
                metrics={sourceMetrics(selectedPlatform, execution, selectedPlatformHasExecution)}
                accent="green"
                onClickApproval={selectedPlatformUrl ? () => openSourceCreatePage(selectedPlatform) : undefined}
                actionLabel={selectedPlatformUrl ? sourceActionLabel : undefined}
              />
            )}

            {activeNodes.map((node) => (
              <NodeBlock
                key={node.id}
                node={node}
                source={selectedSourceKey}
                selectedApprovalId={selectedApprovalId}
                onSelectApproval={setSelectedApprovalId}
              />
            ))}
          </Box>
        </main>

        <ApprovalDialog
          approval={selectedApproval}
          open={Boolean(selectedApprovalId && selectedApproval)}
          approver={approver}
          decisionNote={decisionNote}
          onApproverChange={setApprover}
          onDecisionNoteChange={setDecisionNote}
          onClose={() => setSelectedApprovalId('')}
          onApprove={() => approveMutation.mutate()}
          onReject={() => rejectMutation.mutate()}
          pending={approveMutation.isPending || rejectMutation.isPending}
          error={approvalError}
        />
      </Box>
    </Box>
  );
}

function WorkflowCard({
  index,
  title,
  subtitle,
  icon,
  status,
  metrics,
  accent,
  compact = false,
  actionLabel,
  onClickApproval,
}: {
  index: number;
  title: string;
  subtitle: string;
  icon: ReactNode;
  status: NodeStatus;
  metrics: string[];
  accent: 'green' | 'blue' | 'purple';
  compact?: boolean;
  actionLabel?: string;
  onClickApproval?: () => void;
}) {
  return (
    <Box className={`workflow-card ${status} accent-${accent} ${compact ? 'compact' : ''} ${onClickApproval ? 'clickable' : ''}`} onClick={onClickApproval}>
      <Box className="workflow-card-icon">{icon}</Box>
      <Box className="workflow-card-body">
        <Box className="workflow-card-head">
          <Box className="step-badge">{index}</Box>
          <Box>
            <Typography className="workflow-card-title">{title}</Typography>
            <Typography className="workflow-card-subtitle">{subtitle}</Typography>
          </Box>
          <StatusDot status={status} label={actionLabel} />
        </Box>
        <Box className="workflow-card-metrics">
          {metrics.map((metric) => <MetricItem key={metric} metric={metric} />)}
        </Box>
      </Box>
    </Box>
  );
}

function MetricItem({ metric }: { metric: string }) {
  const knownLabels = [
    'Incident ID',
    'Support Level',
    'Next Stage',
    'PII Scan',
    'Health Check',
    'Report Status',
    'Response Time',
    'Target Agent',
    'Recommended Fixes',
    'Payload',
    'Validation',
    'Latency',
    'Fields',
    'Fingerprint',
    'Security',
    'Risk',
    'Priority',
    'Source',
    'Confidence',
    'Category',
    'Status',
    'Reason',
    'Route',
    'Approval',
    'Approver',
    'Sections',
    'Delivery',
    'Target',
    'PR',
    'LLM Tokens',
  ];
  const label = knownLabels.find((item) => metric === item || metric.startsWith(`${item} `));
  if (!label || metric === label) {
    return <span className="metric-item"><span className="metric-value">{metric}</span></span>;
  }
  return (
    <span className="metric-item">
      <span className="metric-key">{label}</span>
      <span className="metric-value">{metric.slice(label.length).trim()}</span>
    </span>
  );
}

function StatusDot({ status, label }: { status: NodeStatus; label?: string }) {
  const text = label || (status === 'completed' ? 'Done' : status === 'running' ? 'In Progress' : status === 'skipped' ? 'Skipped' : status === 'failed' ? 'Failed' : 'Pending');
  return <span className={`status-pill ${status}`}>{text}</span>;
}

function SummaryStat({ label, value }: { label: string; value: string | number }) {
  return (
    <Box className="summary-stat">
      <span>{label}</span>
      <b>{value}</b>
    </Box>
  );
}

function NodeBlock({
  node,
  source,
  selectedApprovalId,
  onSelectApproval,
}: {
  node: DisplayNode;
  source: string;
  selectedApprovalId: string;
  onSelectApproval: (value: string) => void;
}) {
  if (node.id === 'categorization') {
    return (
      <Box className="workflow-sequence">
        <WorkflowCard
          index={node.index}
          title={node.title}
          subtitle={node.subtitle}
          icon={node.icon}
          status={node.status}
          metrics={node.metrics}
          accent={node.accent}
        />
        {/* Spine that fans out from Categorization into L2 and L3 branches */}
        <Box className="branch-spine">
          <span className="branch-spine-line" />
          <span className="branch-spine-h" />
        </Box>
        <Box className="branch-row">
          <BranchRcaCard
            label="L2"
            title={node.l2Status === 'skipped' ? 'L2 RCA (Skipped)' : 'L2 RCA'}
            subtitle="Medium Complexity"
            status={node.l2Status ?? 'pending'}
            icon={<SettingsSuggestOutlinedIcon />}
            metrics={node.l2Metrics ?? ['Waiting for L2 RCA result']}
          />
          <BranchRcaCard
            label="L3"
            title={node.l3Status === 'skipped' ? 'L3 RCA (Skipped)' : 'L3 RCA'}
            subtitle="High Complexity"
            status={node.l3Status ?? 'pending'}
            icon={<CodeOutlinedIcon />}
            metrics={node.l3Metrics ?? ['Waiting for categorization result']}
          />
        </Box>
        <Box className="merge-spine">
          <span className="merge-line" />
          <span className="merge-node" />
          <span className="merge-line" />
        </Box>
      </Box>
    );
  }

  if (node.id === 'db_fix') {
    const isCodeFixAgent = node.agentType === 'code_fix';
    const agentSubtitle = node.agentType || 'Awaiting agent selection';
    const agentIcon = isCodeFixAgent ? <CodeOutlinedIcon /> : node.icon;
    const waiting = node.status !== 'completed' && node.status !== 'failed' && (node.approvalStatus === 'WAITING_FOR_APPROVAL' || Boolean(selectedApprovalId));
    const followupStatus = node.followupStatus ?? (node.status === 'completed' ? 'completed' : node.status === 'failed' ? 'skipped' : 'pending');
    const approvalGateStatus: NodeStatus = node.status === 'failed' ? 'failed' : waiting ? 'running' : followupStatus;
    return (
      <Box className="workflow-sequence">
        <Box className="connector-line" />
        <ApprovalGateCard
          index={node.index - 1}
          approvalId={node.approvalId}
          onOpen={node.approvalId ? () => onSelectApproval(node.approvalId) : undefined}
          status={approvalGateStatus}
          metrics={approvalMetrics(node)}
        />
        <Box className="connector-line" />
        <WorkflowCard
          index={node.index}
          title="Fix Agent"
          subtitle={agentSubtitle}
          icon={agentIcon}
          status={waiting ? 'pending' : node.status}
          metrics={node.metrics}
          accent={node.accent}
        />
        <Box className="connector-line connector-wide" />
        <WorkflowCard
          index={node.index + 1}
          title="Verification"
          subtitle="Verify remediation result"
          icon={<CheckCircleOutlineIcon />}
          status={followupStatus}
          metrics={verificationMetrics(node)}
          accent="green"
        />
        <Box className="connector-line" />
        <WorkflowCard
          index={node.index + 2}
          title="Response Builder"
          subtitle="Prepare RCA & remediation summary"
          icon={<DescriptionOutlinedIcon />}
          status={followupStatus}
          metrics={responseBuilderMetrics(node)}
          accent="purple"
        />
        <Box className="connector-line connector-wide" />
        <BottomNotificationRow source={source} node={node} completed={followupStatus === 'completed'} />
      </Box>
    );
  }

  if (node.id === 'servicenow' || node.id === 'jira' || node.id === 'github') {
    return null;
  }

  return (
    <Box className="workflow-sequence">
      <WorkflowCard
        index={node.index}
        title={node.title}
        subtitle={node.subtitle}
        icon={node.icon}
        status={node.status}
        metrics={node.metrics}
        accent={node.accent}
      />
      {node.downstream && <Box className="connector-line" />}
    </Box>
  );
}
function BranchRcaCard(props: {
  label: string;
  title: string;
  subtitle: string;
  status: NodeStatus;
  icon: ReactNode;
  metrics: string[];
  onClick?: () => void;
}) {
  return (
    <Box className={`branch-card ${props.status} ${props.onClick ? 'clickable' : ''}`} onClick={props.onClick}>
      <Box className="branch-tag">{props.label}</Box>
      <Box className="workflow-card accent-blue compact">
        <Box className="workflow-card-icon">{props.icon}</Box>
        <Box className="workflow-card-body">
          <Box className="workflow-card-head">
            <Box className="step-badge">6</Box>
            <Box>
              <Typography className="workflow-card-title">{props.title}</Typography>
              <Typography className="workflow-card-subtitle">{props.subtitle}</Typography>
            </Box>
            <StatusDot status={props.status} />
          </Box>
          <Box className="workflow-card-metrics">
            {props.metrics.map((metric) => <MetricItem key={metric} metric={metric} />)}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

function ApprovalGateCard({
  index,
  approvalId,
  onOpen,
  status = approvalId ? 'running' : 'pending',
  metrics,
}: {
  index: number;
  approvalId: string;
  onOpen?: () => void;
  status?: NodeStatus;
  metrics: string[];
}) {
  const subtitleText =
    status === 'completed'
      ? 'Approval completed and remediation executed'
      : status === 'failed'
      ? 'Remediation plan failed — no approval required'
      : approvalId
      ? `Remediation ${approvalId.slice(0, 8)} awaiting approval`
      : 'Waiting for a remediation plan';
  return (
    <Box className={`approval-gate ${onOpen ? 'clickable' : ''}`} onClick={onOpen}>
      <Box className={`approval-gate-card${status === 'failed' ? ' failed-gate' : ''}`}>
        <Box className="approval-gate-icon"><SecurityOutlinedIcon /></Box>
        <Box className="workflow-card-body">
          <Box className="workflow-card-head">
            <Box className="step-badge">{index}</Box>
            <Box>
              <Typography className="workflow-card-title">Human Approval</Typography>
              <Typography className="workflow-card-subtitle">{subtitleText}</Typography>
            </Box>
          </Box>
          <Box className="workflow-card-metrics approval-metrics">
            {metrics.map((metric) => <MetricItem key={metric} metric={metric} />)}
          </Box>
        </Box>
        <StatusDot status={status} label={approvalId && status === 'running' ? 'Awaiting' : undefined} />
      </Box>
    </Box>
  );
}

function getSourceCreateUrl(platform?: SourcePlatform) {
  if (!platform) return;
  if (platform.create_url) return platform.create_url;
  const normalizedSource = sourceKey(platform.source);
  const baseUrl =
    platform.instance_url
    || platform.project_url
    || platform.repo_url
    || (normalizedSource === 'jira' ? import.meta.env.VITE_JIRA_INSTANCE || import.meta.env.VITE_JIRA_URL : undefined);
  if (!baseUrl) return;
  const base = baseUrl.replace(/\/+$/, '');
  let url = base;
  if (normalizedSource === 'servicenow') {
    url = `${base}/nav_to.do?uri=incident.do%3Fsys_id%3D-1`;
  } else if (normalizedSource === 'jira') {
    url = `${base}/secure/CreateIssue!default.jspa`;
  } else if (normalizedSource === 'github') {
    url = `${base}/issues/new`;
  }
  return url;
}

function openSourceCreatePage(platform?: SourcePlatform) {
  const url = getSourceCreateUrl(platform);
  if (!url) return;
  window.open(url, '_blank', 'noopener,noreferrer');
}

function BottomNotificationRow({ source, node, completed = false }: { source: string; node: DisplayNode; completed?: boolean }) {
  const channel = notificationChannel(source);
  const alternates = notificationAlternates(source);
  const baseIndex = node.index + 3;
  // Fix Agent=8, Verification=9, Response Builder=10, Notifications=11,12,13.
  return (
    <Box className="bottom-notifications">
      <WorkflowCard
        index={baseIndex}
        title={`${channel.label} Notification`}
        subtitle={channel.subtitle}
        icon={channel.icon}
        status={completed ? 'completed' : 'pending'}
        metrics={notificationMetrics(node, source, completed)}
        accent="green"
        compact
      />
      {alternates.map((item, index) => (
        <WorkflowCard
          key={item.source}
          index={baseIndex + 1 + index}
          title={`${item.label} Notification`}
          subtitle="Not selected by current backend source"
          icon={item.icon}
          status="skipped"
          metrics={notificationMetrics(node, item.source, false)}
          accent={item.source === 'jira' ? 'blue' : 'purple'}
          compact
        />
      ))}
    </Box>
  );
}

function ApprovalDialog({
  approval,
  open,
  approver,
  decisionNote,
  onApproverChange,
  onDecisionNoteChange,
  onClose,
  onApprove,
  onReject,
  pending,
  error,
}: {
  approval?: RemediationPlan;
  open: boolean;
  approver: string;
  decisionNote: string;
  onApproverChange: (value: string) => void;
  onDecisionNoteChange: (value: string) => void;
  onClose: () => void;
  onApprove: () => void;
  onReject: () => void;
  pending: boolean;
  error: string;
}) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle className="approval-title">Human Approval</DialogTitle>
      <DialogContent className="approval-content">
        <Typography className="approval-summary">{approval?.issue_summary ?? 'No remediation plan selected.'}</Typography>
        <Box className="approval-grid">
          <Meta label="Severity" value={approval?.severity ?? 'N/A'} />
          <Meta label="Confidence" value={approval ? `${Math.round(approval.confidence * 100)}%` : 'N/A'} />
          <Meta label="Recommended action" value={approval?.recommended_action ?? 'N/A'} />
          <Meta label="Expected impact" value={approval?.expected_impact ?? 'N/A'} />
          <Meta label="Execution time" value={approval?.estimated_execution_time ?? 'N/A'} />
          <Meta label="Risk level" value={approval?.risk_level ?? 'N/A'} />
        </Box>
        <Divider className="approval-divider" />
        <TextField label="Approver" value={approver} onChange={(event) => onApproverChange(event.target.value)} fullWidth size="small" />
        <TextField
          label="Decision reason"
          value={decisionNote}
          onChange={(event) => onDecisionNoteChange(event.target.value)}
          fullWidth
          size="small"
          multiline
          minRows={3}
          sx={{ mt: 1.5 }}
        />
        {error ? <Alert severity="error" className="approval-error">{error}</Alert> : null}
        {approval?.status && <Box className="approval-status-line">Status: <b>{approval.status}</b></Box>}
      </DialogContent>
      <DialogActions className="approval-actions">
        <Button onClick={onClose} startIcon={<CloseOutlinedIcon />} disabled={pending}>Close</Button>
        <Button onClick={onReject} color="inherit" startIcon={<WarningAmberOutlinedIcon />} disabled={pending}>Reject</Button>
        <Button onClick={onApprove} variant="contained" startIcon={<CheckOutlinedIcon />} disabled={pending || !approval}>Approve</Button>
      </DialogActions>
    </Dialog>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <Box className="meta-row">
      <span>{label}</span>
      <b>{value}</b>
    </Box>
  );
}

function hasRecordData(value: Record<string, unknown>) {
  return Boolean(value && Object.keys(value).length);
}

function normaliseStatus(value: unknown) {
  return String(value ?? '').trim().toUpperCase();
}

function isCompleteStatus(value: unknown) {
  return ['COMPLETED', 'SUCCESS', 'SUCCEEDED', 'SUCCESSFUL', 'EXECUTED', 'DONE', 'RESOLVED', 'HEALTHY', 'PASSED', 'READY_FOR_EXECUTION'].includes(normaliseStatus(value));
}

function isWaitingStatus(value: unknown) {
  return ['WAITING_FOR_APPROVAL', 'PENDING_APPROVAL', 'APPROVAL_REQUIRED', 'READY_FOR_APPROVAL', 'PENDING'].includes(normaliseStatus(value));
}

function isFailedStatus(value: unknown) {
  return ['FAILED', 'ERROR', 'EXECUTION_FAILED', 'REJECTED', 'BLOCKED'].includes(normaliseStatus(value));
}

function routeLevel(execution: ReturnType<typeof deriveExecution>) {
  return normaliseStatus(execution.categorisation.support_level || execution.categorisation.level || execution.categorisation.rca_level);
}

function normaliseAgentType(value: unknown) {
  const agent = String(value || '').trim().toLowerCase();
  if (!agent) return undefined;
  if (agent === 'codefix' || agent === 'codefix_agent' || agent === 'code_fix_agent') return 'code_fix';
  if (agent === 'db_fix_agent' || agent === 'database_fix_agent') return 'db_fix';
  return agent;
}

function percentMetric(label: string, value: unknown) {
  if (typeof value !== 'number') return undefined;
  return `${label} ${Math.round(value <= 1 ? value * 100 : value)}%`;
}

function textMetric(label: string, value: unknown) {
  if (value === undefined || value === null || value === '') return undefined;
  return `${label} ${String(value)}`;
}

function titleCaseValue(value: unknown) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function durationMetric(label: string, value: unknown) {
  if (typeof value !== 'number') return undefined;
  if (value >= 1000) return `${label} ${(value / 1000).toFixed(1)} s`;
  return `${label} ${Math.round(value)} ms`;
}

function boolMetric(label: string, value: unknown) {
  if (typeof value !== 'boolean') return undefined;
  return `${label} ${value ? 'Yes' : 'No'}`;
}

function shortValue(value: unknown, max = 24) {
  const text = String(value ?? '').trim();
  if (!text) return undefined;
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function compactMetrics(metrics: Array<string | undefined>) {
  return metrics.filter(Boolean) as string[];
}

function tokenMetricValue(usage: unknown, fallback?: unknown) {
  if (typeof usage === 'number') return `LLM Tokens ${usage.toLocaleString()}`;
  const value = asRecord(usage);
  const total = firstValue(value.total_tokens, asRecord(value.tokens).total, fallback);
  if (typeof total === 'number') {
    const suffix = value.measurement === 'partial' ? ' (Partial)' : '';
    return `LLM Tokens ${total.toLocaleString()}${suffix}`;
  }
  if (Number(value.calls || 0) > 0) return 'LLM Tokens Unavailable';
  if (value.measurement === 'not_used') return 'LLM Tokens 0';
  return undefined;
}

function combinedTokenMetric(...usages: unknown[]) {
  const present = usages.map(asRecord).filter((usage) => Object.keys(usage).length);
  if (!present.length) return undefined;
  let total = 0;
  let hasTotal = false;
  let unavailableCall = false;
  for (const usage of present) {
    const value = firstValue(usage.total_tokens, asRecord(usage.tokens).total);
    if (typeof value === 'number') {
      total += value;
      hasTotal = true;
    } else if (Number(usage.calls || 0) > 0) {
      unavailableCall = true;
    }
  }
  if (!hasTotal) return unavailableCall ? 'LLM Tokens Unavailable' : 'LLM Tokens 0';
  return `LLM Tokens ${total.toLocaleString()}${unavailableCall ? ' (Partial)' : ''}`;
}

function firstValue(...values: unknown[]) {
  return values.find((value) => value !== undefined && value !== null && value !== '');
}

function countWorkflowSteps(nodes: WorkflowNode[]) {
  const visibleBackendSteps = nodes.filter((node) => !['l1_placeholder', 'codefix'].includes(node.id)).length;
  return visibleBackendSteps || 13;
}

function deriveOverallConfidence(execution: ReturnType<typeof deriveExecution>) {
  const candidates = [
    execution.response.overall_confidence,
    execution.l3Rca.confidence_score,
    execution.l3Rca.confidence,
    execution.categorisation.confidence,
    execution.l2Response.confidence,
    execution.dbExecution.confidence,
  ];
  for (const value of candidates) {
    if (typeof value === 'number') return value <= 1 ? Math.round(value * 100) : Math.round(value);
  }
  return undefined;
}

function deriveWorkflowTimeMs(execution: ReturnType<typeof deriveExecution>) {
  const responseMetrics = asRecord(execution.response.metrics);
  const dbStages = asRecord(execution.dbMetrics.stage_durations_ms);
  const candidates = [
    responseMetrics.total_duration_ms,
    responseMetrics.duration_ms,
    execution.dbMetrics.total_duration_ms,
    dbStages.total,
  ];
  return candidates.find((value) => typeof value === 'number') as number | undefined;
}

function formatPercentSummary(value: unknown) {
  if (typeof value !== 'number') return '0%';
  return `${Math.round(value <= 1 ? value * 100 : value)}%`;
}

function formatDurationSummary(value: unknown) {
  if (typeof value !== 'number') return '0 s';
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${Math.round(value)} ms`;
}

function buildDisplayNodes(
  nodes: WorkflowNode[],
  execution: ReturnType<typeof deriveExecution>,
  displaySource: string,
  latestApproval?: RemediationPlan,
  selectedApproval?: RemediationPlan,
) {
  const statusById = new Map<string, NodeStatus>();
  const approvalStatus = latestApproval?.status || selectedApproval?.status || String(execution.fixAgent.status || execution.dbExecution.status || '');
  const hasExecution = hasRecordData(execution.response);
  const hasCategorisation = hasRecordData(execution.categorisation);
  const hasL2Result = hasRecordData(execution.l2) || hasRecordData(execution.l2Response) || hasRecordData(execution.rca);
  const hasL3Result = hasRecordData(execution.l3Rca);
  const hasFixPlan = hasRecordData(execution.fixAgent);
  const hasFixExecution = hasRecordData(execution.fixAgentExecution) || hasRecordData(execution.codefix);
  const hasDbResult = hasRecordData(execution.dbExecution);
  const level = routeLevel(execution);
  const dbStatus = firstValue(execution.dbExecution.overall_status, execution.dbExecution.status, execution.dbMetrics.overall_status, execution.dbVerification.status);
  const codefixStatus = firstValue(execution.codefix.status, execution.fixAgent.status, execution.response.status);
  const dbComplete = hasDbResult && (isCompleteStatus(dbStatus) || isCompleteStatus(execution.dbVerification.status) || execution.dbMetrics.verification_passed === true);
  const codefixComplete = (hasFixExecution || hasFixPlan) && (isCompleteStatus(codefixStatus) || Boolean(firstValue(execution.codefix.pr_url, execution.codefix.pull_request_url, execution.fixAgentExecution.pr_url)));
  const fixWaiting = isWaitingStatus(approvalStatus) || isWaitingStatus(execution.response.status) || isWaitingStatus(codefixStatus);
  const fixFailed = isFailedStatus(dbStatus) || isFailedStatus(codefixStatus);
  const isL3 = level === 'L3';
  const isL2 = level === 'L2';
  const configuredAgent = firstValue(
    execution.fixAgent.agent_type,
    execution.dbExecution.agent,
    execution.rca.recommended_agent,
  );
  const agentType = normaliseAgentType(configuredAgent) || (isL3 ? 'code_fix' : undefined);

  statusById.set('connector', hasExecution ? 'completed' : 'pending');
  statusById.set('normalizer', hasExecution ? 'completed' : 'pending');
  const guardrails = asRecord(execution.response.guardrails);
  const hasGuardrailData = hasRecordData(guardrails);
  const guardrailStatus: NodeStatus = hasGuardrailData ? (guardrails.allowed === false ? 'skipped' : 'completed') : hasExecution ? 'running' : 'pending';
  statusById.set('guardrails', guardrailStatus);
  statusById.set('security_guardrails', guardrailStatus);
  statusById.set('categorization', hasCategorisation ? 'completed' : hasExecution ? 'running' : 'pending');
  statusById.set('l2_rca', isL2 ? (hasL2Result ? 'completed' : hasCategorisation ? 'running' : 'pending') : hasCategorisation ? 'skipped' : 'pending');
  statusById.set('l3_rca', isL3 ? (hasL3Result || hasFixPlan || hasFixExecution ? 'completed' : hasCategorisation ? 'running' : 'pending') : hasCategorisation ? 'skipped' : 'pending');
  statusById.set('fix_agent', codefixComplete || dbComplete ? 'completed' : fixFailed ? 'failed' : fixWaiting || hasFixPlan || hasFixExecution ? 'running' : 'pending');
  statusById.set('db_fix', codefixComplete || dbComplete ? 'completed' : fixFailed ? 'failed' : fixWaiting || hasFixPlan || hasFixExecution ? 'running' : isL2 && hasL2Result ? 'pending' : isL3 && hasL3Result ? 'pending' : 'pending');

  const selectedSource = sourceKey(execution.normalised.source || execution.response.source);
  ['servicenow', 'jira', 'github'].forEach((source) => {
    statusById.set(source, source === selectedSource && (codefixComplete || dbComplete) ? 'completed' : 'skipped');
  });

  const l2Status = statusById.get('l2_rca') ?? 'pending';
  const l3Status = statusById.get('l3_rca') ?? 'pending';
  const followupStatus: NodeStatus = codefixComplete || dbComplete ? 'completed' : fixFailed ? 'skipped' : 'pending';

  // Fixed step map so numbering is always sequential and gapless:
  // 1=Source, 2=Connector, 3=Normalizer, 4=Guardrails, 5=Categorization,
  // 6=L2/L3 branch, 7=Human Approval, 8=Fix Agent, 9=Verification,
  // 10=Response Builder, 11/12/13=Notifications.
  const fixedIndexMap: Record<string, number> = {
    connector: 2, normalizer: 3, guardrails: 4, security_guardrails: 4,
    categorization: 5, db_fix: 8,
  };

  const config: DisplayNode[] = nodes.map((node) => {
    const id = node.id === 'security_guardrails' ? 'guardrails' : node.id;
    const status = statusById.get(id) ?? 'pending';
    const baseIndex = fixedIndexMap[node.id] ?? fixedIndexMap[id] ?? 7;
    return {
      id,
      index: baseIndex,
      status,
      title: node.label,
      subtitle: node.service,
      icon: iconForNode(id),
      metrics: metricsForNode(id, execution, displaySource),
      accent: accentForNode(id),
      downstream: id !== 'db_fix',
      approvalId: id === 'db_fix' ? latestApproval?.approval_id ?? selectedApproval?.approval_id ?? String(execution.fixAgent.approval_id || '') : '',
      approvalStatus,
      l2Status,
      l3Status,
      l2Metrics: metricsForNode('l2_rca', execution, displaySource),
      l3Metrics: metricsForNode('l3_rca', execution, displaySource),
      dbVerification: execution.dbVerification,
      dbMetrics: execution.dbMetrics,
      dbExecution: execution.dbExecution,
      fixAgent: execution.fixAgent,
      fixAgentExecution: execution.fixAgentExecution,
      l3Rca: execution.l3Rca,
      codefix: execution.codefix,
      normalised: execution.normalised,
      agentType,
      followupStatus,
      routeLevel: level,
    };
  });

  return config.filter((node) => ['connector', 'normalizer', 'guardrails', 'categorization', 'db_fix'].includes(node.id));
}

function metricsForNode(id: string, execution: ReturnType<typeof deriveExecution>, displaySource: string) {
  if (id === 'connector') {
    const metrics = compactMetrics([
      textMetric('Payload', execution.response.source_event_id || execution.normalised.source_event_id ? 'Received' : undefined),
      textMetric('Validation', execution.response.validation_status || execution.response.auth_status || (hasRecordData(execution.response) ? 'Passed' : undefined)),
      textMetric('Event', execution.response.source_event_id || execution.normalised.source_event_id),
      textMetric('Source', sourceLabel(sourceKey(execution.normalised.source || execution.response.source || displaySource))),
    ]);
    return metrics.length ? metrics.slice(0, 3) : ['Waiting for backend connector result'];
  }
  if (id === 'normalizer') {
    const fieldCount = Object.entries(execution.normalised).filter(([, value]) => value !== undefined && value !== null && value !== '').length;
    const metrics = compactMetrics([
      textMetric('Fields', fieldCount || undefined),
      textMetric('Fingerprint', shortValue(execution.normalised.fingerprint, 12)),
      textMetric('Error', shortValue(execution.normalised.error_type || execution.normalised.message, 28)),
    ]);
    return metrics.length ? metrics : ['Waiting for backend normalizer result'];
  }
  if (id === 'guardrails') return guardrailMetrics(execution);
  if (id === 'categorization') {
    const level = routeLevel(execution);
    const metrics = compactMetrics([
      percentMetric('Confidence', execution.categorisation.confidence),
      level ? `Support Level ${level}` : undefined,
      tokenMetricValue(execution.tokenUsage.categorisation || execution.categorisation.token_usage),
      textMetric('Category', titleCaseValue(execution.categorisation.category || execution.categorisation.issue_category)),
      textMetric('Agent', execution.categorisation.selected_agent),
    ]);
    return metrics.length ? metrics.slice(0, 3) : ['Waiting for categorization result'];
  }
  if (id === 'l2_rca') {
    const level = routeLevel(execution);
    if (!level) return ['Waiting for categorization result'];
    if (level !== 'L2') {
      return compactMetrics([
        textMetric('Status', 'Skipped'),
        textMetric('Reason', level ? `Routed to ${level}` : undefined),
        textMetric('Next Stage', level === 'L3' ? 'L3 RCA' : undefined),
      ]);
    }
    const metrics = compactMetrics([
      percentMetric('Confidence', execution.l2Response.confidence || execution.rca.confidence),
      durationMetric('LLM Latency', asRecord(execution.l2Response.metrics).llm_latency_ms || asRecord(execution.l2Response.execution).duration_ms || asRecord(execution.l2Response.metrics).duration_ms),
      tokenMetricValue(execution.tokenUsage.l2_rca || execution.l2Response.token_usage_details,
        asRecord(execution.l2Response.metrics).token_usage),
      textMetric('Decision', execution.l2Response.decision || execution.rca.recommended_agent || execution.rca.problem_domain),
    ]);
    return metrics.length ? metrics : ['Waiting for L2 RCA result'];
  }
  if (id === 'l3_rca') {
    const level = routeLevel(execution);
    if (!level) return ['Waiting for categorization result'];
    if (level !== 'L3') {
      return compactMetrics([
        textMetric('Status', 'Skipped'),
        textMetric('Reason', level ? `Routed to ${level}` : undefined),
        textMetric('Route', 'Not selected'),
      ]);
    }
    const metrics = compactMetrics([
      textMetric('Status', execution.l3Rca.status || (hasRecordData(execution.l3Rca) ? 'Completed' : execution.response.status)),
      percentMetric('Confidence', execution.l3Rca.confidence_score || execution.l3Rca.confidence),
      combinedTokenMetric(execution.tokenUsage.l3_context, execution.tokenUsage.l3_rca || execution.l3Rca.token_usage),
      textMetric('Evidence', shortValue(firstValue(execution.l3Rca.buggy_file, execution.normalised.file_path), 36)),
      textMetric('Route', 'L3'),
    ]);
    return metrics.length ? metrics.slice(0, 3) : ['Waiting for L3 RCA result'];
  }
  if (id === 'fix_agent' || id === 'db_fix') {
    const level = routeLevel(execution);
    if (level === 'L3') {
      const metrics = compactMetrics([
        textMetric('Target Agent', execution.fixAgent.agent_type || 'code_fix'),
        textMetric('Status', firstValue(execution.codefix.status, execution.fixAgent.status, execution.response.status)),
        tokenMetricValue(execution.tokenUsage.codefix || execution.codefix.token_usage || execution.fixAgentExecution.token_usage),
        textMetric('PR', shortValue(firstValue(execution.codefix.pr_url, execution.codefix.pull_request_url, execution.fixAgentExecution.pr_url), 36)),
        textMetric('Approval', execution.fixAgent.approval_status || execution.fixAgent.status),
      ]);
      return metrics.length ? metrics.slice(0, 3) : ['Waiting for code fix result'];
    }
    const metrics = compactMetrics([
      textMetric('Recommended Fixes', firstValue(execution.dbExecution.recommended_fixes, execution.dbExecution.actions_count, execution.dbExecution.plan_status)),
      textMetric('Target Agent', firstValue(execution.dbExecution.agent, execution.rca.recommended_agent, execution.fixAgent.agent_type)),
      textMetric('Status', firstValue(execution.dbExecution.overall_status, execution.dbVerification.status, execution.dbExecution.status, execution.fixAgent.status)),
    ]);
    return metrics.length ? metrics : ['Waiting for remediation result'];
  }
  if (id === 'servicenow' || id === 'jira' || id === 'github') {
    return notificationMetrics({ dbExecution: execution.dbExecution, dbMetrics: execution.dbMetrics, fixAgent: execution.fixAgent, codefix: execution.codefix, normalised: execution.normalised }, id, false);
  }
  return ['Waiting for backend result'];
}

function guardrailMetrics(execution: ReturnType<typeof deriveExecution>) {
  const guardrails = asRecord(execution.response.guardrails);
  const checks = Array.isArray(guardrails.checks) ? guardrails.checks as Array<Record<string, unknown>> : [];
  const securityCheck = checks.find((check) => /security|policy/i.test(String(check.display_label || check.name || '')));
  const piiCheck = checks.find((check) => /pii|privacy/i.test(String(check.display_label || check.name || '')));
  const statusForCheck = (check: Record<string, unknown> | undefined) => check
    ? String(check.status || (check.passed === false ? 'Failed' : 'Passed'))
    : undefined;
  const metrics = compactMetrics([
    textMetric('Security', statusForCheck(securityCheck) || guardrails.security_status || guardrails.policy_status || (guardrails.allowed === false ? 'Blocked' : undefined)),
    textMetric('PII Scan', statusForCheck(piiCheck) || guardrails.pii_scan || guardrails.pii_status),
    textMetric('Risk', guardrails.risk_level || guardrails.risk ? titleCaseValue(guardrails.risk_level || guardrails.risk) : undefined),
  ]);

  if (!metrics.length && Object.keys(guardrails).length) {
    const totalChecks = Number(guardrails.total_checks || 0);
    const passedChecks = Number(guardrails.passed_checks || 0);
    if (totalChecks > 0) metrics.push(`Security ${passedChecks}/${totalChecks}`);
    if (guardrails.allowed !== undefined) metrics.push(`Policy ${guardrails.allowed === false ? 'Blocked' : 'Allowed'}`);
  }

  return metrics.slice(0, 3).length ? metrics.slice(0, 3) : ['Waiting for backend guardrail result'];
}

function accentForNode(id: string) {
  if (id === 'guardrails' || id === 'servicenow') return 'green';
  if (id === 'l2_rca' || id === 'connector') return 'blue';
  return 'purple';
}

function iconForNode(id: string) {
  if (id === 'guardrails') return <SecurityOutlinedIcon />;
  if (id === 'categorization') return <AccountTreeOutlinedIcon />;
  if (id === 'l2_rca' || id === 'fix_agent') return <SettingsSuggestOutlinedIcon />;
  if (id === 'l3_rca') return <CodeOutlinedIcon />;
  if (id === 'db_fix') return <DatabaseOutlinedIcon />;
  if (id === 'servicenow') return <PaperPlaneOutlinedIcon />;
  if (id === 'jira') return <BugReportOutlinedIcon />;
  if (id === 'github') return <CodeOutlinedIcon />;
  return <HourglassTopIcon />;
}

function sourceBadge(source: string) {
  if (source === 'jira') return 'JI';
  if (source === 'github') return 'GH';
  return 'SN';
}

function sourceKey(source: unknown) {
  const value = String(source || '').toLowerCase();
  if (value === 'github_issue') return 'github';
  if (value === 'jira' || value === 'github' || value === 'servicenow') return value;
  return 'servicenow';
}

function sourceLabel(source: string) {
  if (source === 'jira') return 'Jira';
  if (source === 'github' || source === 'github_issue') return 'GitHub';
  return 'ServiceNow';
}

function sourceMetrics(platform: SourcePlatform, execution: ReturnType<typeof deriveExecution>, includeExecution: boolean) {
  if (!includeExecution) return [`Source ${sourceLabel(sourceKey(platform.source))}`];
  const metrics = compactMetrics([
    textMetric('Incident ID', firstValue(execution.normalised.incident_id, execution.normalised.external_id, execution.categorisation.ticket_id, execution.response.event_id, execution.response.source_event_id)),
    textMetric('Priority', firstValue(execution.normalised.priority, execution.categorisation.priority)),
    textMetric('Source', sourceLabel(sourceKey(firstValue(execution.normalised.source, execution.response.source, platform.source)))),
  ]);
  return metrics.length ? metrics : ['Waiting for backend source event'];
}

function approvalMetrics(node: DisplayNode) {
  const dbExecution = asRecord(node.dbExecution);
  const fixAgent = asRecord(node.fixAgent);
  const approval = asRecord(dbExecution.approval || dbExecution.approval_request || fixAgent.approval || fixAgent.approval_request);
  const metrics = compactMetrics([
    textMetric('Approval', node.approvalStatus || approval.status || fixAgent.status),
    textMetric('Risk', firstValue(dbExecution.risk, dbExecution.risk_level, approval.risk, fixAgent.risk_level)),
    textMetric('Approver', firstValue(approval.approver, dbExecution.approver, fixAgent.approver)),
  ]);
  return metrics.length ? metrics : ['Waiting for human approval'];
}

function verificationMetrics(node: DisplayNode) {
  const verification = asRecord(node.dbVerification);
  const dbMetrics = asRecord(node.dbMetrics);
  const codefix = asRecord(node.codefix);
  const fixExecution = asRecord(node.fixAgentExecution);
  const metrics = compactMetrics([
    textMetric('Health Check', firstValue(verification.health_check, verification.status, verification.overall_status, codefix.verification_status, fixExecution.verification_status)),
    boolMetric('Validation', dbMetrics.verification_passed),
    textMetric('Status', firstValue(verification.status, verification.overall_status, codefix.status, fixExecution.status, dbMetrics.verification_passed === true ? 'Passed' : undefined)),
  ]);
  return metrics.length ? metrics : ['Waiting for remediation verification'];
}

function responseBuilderMetrics(node: DisplayNode) {
  const dbExecution = asRecord(node.dbExecution);
  const codefix = asRecord(node.codefix);
  const l3Rca = asRecord(node.l3Rca);
  const metrics = compactMetrics([
    textMetric('Report Status', firstValue(dbExecution.report_status, dbExecution.overall_status, dbExecution.status, codefix.status)),
    textMetric('Report', shortValue(firstValue(codefix.rca_report_path, l3Rca.report_path, dbExecution.report_path), 32)),
    percentMetric('Confidence', firstValue(dbExecution.confidence, l3Rca.confidence_score, l3Rca.confidence)),
  ]);
  return metrics.length ? metrics : ['Waiting for RCA summary'];
}

function notificationChannel(source: string) {
  const normalized = source === 'github_issue' ? 'github' : source;
  if (normalized === 'jira') return { source: 'jira', label: 'Jira', subtitle: 'Update issue with resolution', icon: <BugReportOutlinedIcon /> };
  if (normalized === 'github') return { source: 'github', label: 'GitHub', subtitle: 'Update issue with resolution', icon: <CodeOutlinedIcon /> };
  return { source: 'servicenow', label: 'ServiceNow', subtitle: 'Update incident with resolution', icon: <PaperPlaneOutlinedIcon /> };
}

function notificationAlternates(source: string) {
  const active = notificationChannel(source).source;
  return [
    { source: 'servicenow', label: 'ServiceNow', icon: <PaperPlaneOutlinedIcon /> },
    { source: 'jira', label: 'Jira', icon: <BugReportOutlinedIcon /> },
    { source: 'github', label: 'GitHub', icon: <CodeOutlinedIcon /> },
  ].filter((item) => item.source !== active).slice(0, 2);
}

function notificationMetrics(
  node: Pick<DisplayNode, 'dbExecution' | 'dbMetrics' | 'fixAgent' | 'codefix' | 'normalised'>,
  source: string,
  completed: boolean,
) {
  const dbExecution = asRecord(node.dbExecution);
  const dbMetrics = asRecord(node.dbMetrics);
  const fixAgent = asRecord(node.fixAgent);
  const codefix = asRecord(node.codefix);
  const normalised = asRecord(node.normalised);
  const stages = asRecord(dbMetrics.stage_durations_ms);
  const activeSource = notificationChannel(source).source;
  const notification = asRecord(dbExecution[`${activeSource}_notification`] || dbExecution.notification || dbExecution.sn_notification || fixAgent.notification || codefix.notification);
  const delivered = completed || isCompleteStatus(notification.status) || isCompleteStatus(notification.state);

  const metrics = compactMetrics([
    textMetric('Status', firstValue(notification.status, notification.state, dbExecution.notification_status, delivered ? 'Sent' : undefined)),
    textMetric('Reason', firstValue(notification.reason, notification.error, notification.message)),
    durationMetric('Response Time', firstValue(notification.response_time_ms, stages[`${activeSource}_notification`], stages.sn_notification)),
    textMetric('Target', firstValue(notification.target, notification.issue_key, notification.issue_id, normalised.incident_id, normalised.external_id)),
    textMetric('PR', firstValue(notification.pr, notification.pull_request, notification.pr_url, codefix.pr_url, codefix.pull_request_url)),
  ]);

  if (metrics.length) return metrics.slice(0, 3);
  return delivered ? ['Backend completed; no notification details returned'] : [`Waiting for ${sourceLabel(activeSource)} notification result`];
}

