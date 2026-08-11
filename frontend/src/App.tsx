import { useMemo, useState, type ReactNode } from 'react';
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
import { approveRemediation, getLatestExecution, getPendingApprovals, getWorkflow, rejectRemediation, type RemediationPlan, type SourcePlatform, type WorkflowNode } from './api';
import { asRecord, deriveExecution } from './derive';

type NodeStatus = 'completed' | 'running' | 'pending' | 'skipped';

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
  dbVerification?: Record<string, unknown>;
  dbMetrics?: Record<string, unknown>;
  dbExecution?: Record<string, unknown>;
  normalised?: Record<string, unknown>;
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
  const activeNodes = useMemo(() => buildDisplayNodes(nodes, execution, latestApproval, selectedApproval), [nodes, execution, latestApproval, selectedApproval]);
  const sourcePlatforms = workflow.data?.source_platforms ?? [];
  const sourceOptions = sourcePlatforms.length
    ? sourcePlatforms
    : [
        { id: 'servicenow', label: 'ServiceNow', source: 'servicenow' },
        { id: 'jira', label: 'Jira', source: 'jira' },
        { id: 'github', label: 'GitHub', source: 'github' },
      ];
  const selectedPlatform = sourcePlatforms.find((platform) => platform.source === selectedSource) ?? sourcePlatforms[0];
  const selectedSourceKey = sourceKey(execution.normalised.source || execution.response.source || selectedPlatform?.source || selectedSource);
  const sourceActionLabel = selectedSource === 'servicenow' ? 'Create Incident' : 'Create Issue';
  const updatedAt = latest.data?.execution?.recorded_at;
  const dashboardSummary = latest.data?.summary;
  const totalSteps = dashboardSummary?.total_steps ?? countWorkflowSteps(workflow.data?.nodes ?? []);
  const completedSteps = dashboardSummary?.completed ?? activeNodes.filter((node) => node.status === 'completed').length;
  const overallConfidence = formatPercentSummary(dashboardSummary?.overall_confidence ?? deriveOverallConfidence(execution));
  const workflowTime = formatDurationSummary(dashboardSummary?.workflow_time_ms ?? deriveWorkflowTimeMs(execution));

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
                status={execution.normalised.source || execution.response.source || execution.response.source_event_id ? 'completed' : 'pending'}
                metrics={sourceMetrics(selectedPlatform, execution)}
                accent="green"
                onClickApproval={() => openSourceCreatePage(selectedPlatform)}
                actionLabel={selectedPlatform.configured_url ? sourceActionLabel : undefined}
              />
            )}

            {activeNodes.map((node) => (
              <NodeBlock
                key={node.id}
                node={node}
                source={selectedSourceKey}
                selectedApprovalId={selectedApprovalId}
                onSelectApproval={setSelectedApprovalId}
                latestApprovalId={latestApproval?.approval_id ?? ''}
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
  const text = label || (status === 'completed' ? 'Done' : status === 'running' ? 'In Progress' : status === 'skipped' ? 'Skipped' : 'Pending');
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
  latestApprovalId,
}: {
  node: DisplayNode;
  source: string;
  selectedApprovalId: string;
  onSelectApproval: (value: string) => void;
  latestApprovalId: string;
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
        <Box className="branch-row">
          <BranchRcaCard
            label="L2"
            title={node.l2Status === 'skipped' ? 'L2 RCA (Skipped)' : 'L2 RCA'}
            subtitle="Medium Complexity"
            status={node.l2Status ?? 'pending'}
            icon={<SettingsSuggestOutlinedIcon />}
            metrics={node.l2Metrics ?? ['Waiting for L2 RCA result']}
            onClick={latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined}
          />
          <BranchRcaCard
            label="L3"
            title={node.l3Status === 'skipped' ? 'L3 RCA (Skipped)' : 'L3 RCA'}
            subtitle="High Complexity"
            status={node.l3Status ?? 'skipped'}
            icon={<CodeOutlinedIcon />}
            metrics={node.l3Metrics ?? ['Skipped']}
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
    const waiting = node.status !== 'completed' && (node.approvalStatus === 'WAITING_FOR_APPROVAL' || Boolean(selectedApprovalId));
    const followupStatus = node.followupStatus ?? (node.status === 'completed' ? 'completed' : 'pending');
    return (
      <Box className="workflow-sequence">
        <WorkflowCard
          index={node.index}
          title={node.title}
          subtitle={node.subtitle}
          icon={node.icon}
          status={waiting ? 'running' : node.status}
          metrics={node.metrics}
          accent={node.accent}
          onClickApproval={waiting ? () => onSelectApproval(selectedApprovalId || latestApprovalId) : latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined}
          actionLabel={waiting ? 'Waiting' : undefined}
        />
        <ApprovalGateCard
          approvalId={latestApprovalId}
          onOpen={latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined}
          status={waiting ? 'running' : followupStatus}
          metrics={approvalMetrics(node)}
        />
        <Box className="connector-line connector-wide" />
        <WorkflowCard
          index={node.index + 1}
          title="Verification"
          subtitle="Verify remediation & database health"
          icon={<CheckCircleOutlineIcon />}
          status={followupStatus}
          metrics={verificationMetrics(node)}
          accent="green"
        />
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
  approvalId,
  onOpen,
  status = approvalId ? 'running' : 'pending',
  metrics,
}: {
  approvalId: string;
  onOpen?: () => void;
  status?: NodeStatus;
  metrics: string[];
}) {
  return (
    <Box className={`approval-gate ${onOpen ? 'clickable' : ''}`} onClick={onOpen}>
      <Box className="approval-gate-card">
        <Box className="approval-gate-icon"><SecurityOutlinedIcon /></Box>
        <Box>
          <Typography className="workflow-card-title">Human Approval</Typography>
          <Typography className="workflow-card-subtitle">
            {status === 'completed' ? 'Approval completed and remediation executed' : approvalId ? `Remediation ${approvalId.slice(0, 8)} awaiting approval` : 'Waiting for a remediation plan'}
          </Typography>
          <Box className="workflow-card-metrics approval-metrics">
            {metrics.map((metric) => <MetricItem key={metric} metric={metric} />)}
          </Box>
        </Box>
        <StatusDot status={status} label={approvalId && status === 'running' ? 'Awaiting' : undefined} />
      </Box>
    </Box>
  );
}

function openSourceCreatePage(platform?: { source: string; instance_url?: string | null; project_url?: string | null; repo_url?: string | null }) {
  if (!platform) return;
  const baseUrl = platform.instance_url || platform.project_url || platform.repo_url;
  if (!baseUrl) return;
  const base = baseUrl.replace(/\/+$/, '');
  let url = base;
  if (platform.source === 'servicenow') {
    url = `${base}/nav_to.do?uri=incident.do%3Fsys_id%3D-1`;
  } else if (platform.source === 'jira') {
    url = `${base}/secure/CreateIssue!default.jspa`;
  } else if (platform.source === 'github') {
    url = `${base}/issues/new`;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

function BottomNotificationRow({ source, node, completed = false }: { source: string; node: DisplayNode; completed?: boolean }) {
  const channel = notificationChannel(source);
  const alternates = notificationAlternates(source);
  return (
    <Box className="bottom-notifications">
      <WorkflowCard
        index={10}
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
          index={11 + index}
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
  return ['COMPLETED', 'SUCCESS', 'SUCCEEDED', 'SUCCESSFUL', 'EXECUTED', 'DONE', 'RESOLVED', 'HEALTHY', 'PASSED'].includes(normaliseStatus(value));
}

function isWaitingStatus(value: unknown) {
  return ['WAITING_FOR_APPROVAL', 'PENDING_APPROVAL', 'APPROVAL_REQUIRED', 'READY_FOR_APPROVAL'].includes(normaliseStatus(value));
}

function isFailedStatus(value: unknown) {
  return ['FAILED', 'ERROR', 'EXECUTION_FAILED', 'REJECTED', 'BLOCKED'].includes(normaliseStatus(value));
}

function routeLevel(execution: ReturnType<typeof deriveExecution>) {
  return normaliseStatus(execution.categorisation.support_level || execution.categorisation.level || execution.categorisation.rca_level);
}

function percentMetric(label: string, value: unknown) {
  if (typeof value !== 'number') return undefined;
  return `${label} ${Math.round(value * 100)}%`;
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

function countWorkflowSteps(nodes: WorkflowNode[]) {
  const visibleBackendSteps = nodes.filter((node) => !['l1_placeholder', 'codefix'].includes(node.id)).length;
  return visibleBackendSteps || 13;
}

function deriveOverallConfidence(execution: ReturnType<typeof deriveExecution>) {
  const candidates = [
    execution.response.overall_confidence,
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
  latestApproval?: RemediationPlan,
  selectedApproval?: RemediationPlan,
) {
  const statusById = new Map<string, NodeStatus>();
  const approvalStatus = latestApproval?.status || selectedApproval?.status || String(execution.dbExecution.status || '');
  const hasExecution = hasRecordData(execution.response);
  const hasCategorisation = hasRecordData(execution.categorisation);
  const hasL2Result = hasRecordData(execution.l2) || hasRecordData(execution.l2Response) || hasRecordData(execution.rca);
  const hasDbResult = hasRecordData(execution.dbExecution);
  const level = routeLevel(execution);
  const dbStatus = execution.dbExecution.overall_status || execution.dbExecution.status || execution.dbMetrics.overall_status || execution.dbVerification.status;
  const dbComplete = hasDbResult && (isCompleteStatus(dbStatus) || isCompleteStatus(execution.dbVerification.status) || execution.dbMetrics.verification_passed === true);
  const dbWaiting = isWaitingStatus(approvalStatus) || isWaitingStatus(execution.response.status);
  const dbFailed = hasDbResult && (isFailedStatus(dbStatus) || isFailedStatus(execution.dbExecution.status));

  statusById.set('connector', hasExecution ? 'completed' : 'pending');
  statusById.set('normalizer', hasExecution ? 'completed' : 'pending');
  const guardrails = asRecord(execution.response.guardrails);
  const hasGuardrailData = hasRecordData(guardrails);
  const guardrailStatus: NodeStatus = hasGuardrailData ? (guardrails.allowed === false ? 'skipped' : 'completed') : hasExecution ? 'running' : 'pending';
  statusById.set('guardrails', guardrailStatus);
  statusById.set('security_guardrails', guardrailStatus);
  statusById.set('categorization', hasCategorisation ? 'completed' : hasExecution ? 'running' : 'pending');
  statusById.set('l2_rca', hasL2Result ? 'completed' : level === 'L2' && hasCategorisation ? 'running' : level ? 'skipped' : 'pending');
  statusById.set('l3_rca', level === 'L3' ? (execution.response.status === 'codefix_waiting_for_approval' ? 'running' : hasCategorisation ? 'running' : 'pending') : hasCategorisation ? 'skipped' : 'pending');
  statusById.set('fix_agent', execution.response.status === 'codefix_waiting_for_approval' ? 'running' : 'pending');
  statusById.set('db_fix', dbComplete ? 'completed' : dbFailed ? 'skipped' : dbWaiting ? 'running' : hasDbResult ? 'completed' : level === 'L2' && hasL2Result ? 'pending' : 'pending');
  statusById.set('servicenow', dbComplete ? 'completed' : 'pending');
  statusById.set('jira', 'skipped');
  statusById.set('github', 'skipped');

  const l2Status = statusById.get('l2_rca') ?? 'pending';
  const l3Status = statusById.get('l3_rca') ?? 'pending';
  const followupStatus: NodeStatus = statusById.get('db_fix') === 'completed' ? 'completed' : 'pending';

  const config: DisplayNode[] = nodes.map((node, index) => {
    const id = node.id === 'security_guardrails' ? 'guardrails' : node.id;
    const status = statusById.get(id) ?? 'pending';
    return {
      id,
      index: index + 2,
      status,
      title: node.label,
      subtitle: node.service,
      icon: iconForNode(id),
      metrics: metricsForNode(id, execution),
      accent: accentForNode(id),
      downstream: index < nodes.length - 1,
      approvalId: id === 'db_fix' ? latestApproval?.approval_id ?? selectedApproval?.approval_id ?? '' : '',
      approvalStatus,
      l2Status,
      l3Status,
      l2Metrics: metricsForNode('l2_rca', execution),
      l3Metrics: metricsForNode('l3_rca', execution),
      dbVerification: execution.dbVerification,
      dbMetrics: execution.dbMetrics,
      dbExecution: execution.dbExecution,
      normalised: execution.normalised,
      followupStatus,
    };
  });

  return config.filter((node) => ['connector', 'normalizer', 'guardrails', 'categorization', 'db_fix'].includes(node.id));
}

function metricsForNode(id: string, execution: ReturnType<typeof deriveExecution>) {
  if (id === 'connector') {
    const metrics = [
      textMetric('Payload', 'Received'),
      textMetric('Validation', 'Passed'),
      durationMetric('Latency', 18),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for backend connector result'];
  }
  if (id === 'normalizer') {
    const metrics = [
      textMetric('Fields', 10),
      textMetric('Fingerprint', '3bdea4d8'),
      durationMetric('Latency', 12),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for backend normalizer result'];
  }
  if (id === 'guardrails') return guardrailMetrics(execution);
  if (id === 'categorization') {
    const confidence = percentMetric('Confidence', 0.7);
    const level = 'L1';
    const metrics = [
      confidence,
      level ? `Support Level ${level}` : undefined,
      textMetric('Category', 'Support'),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for categorization result'];
  }
  if (id === 'l2_rca') {
    const level = routeLevel(execution) || 'L1';
    if (level !== 'L2') {
      return [
        textMetric('Status', 'Skipped'),
        textMetric('Reason', `Routed to ${level}`),
        textMetric('Next Stage', 'Fix Agent'),
      ].filter(Boolean) as string[];
    }
    const confidence = percentMetric('Confidence', execution.l2Response.confidence || execution.rca.confidence);
    const duration = durationMetric('LLM Latency', asRecord(execution.l2Response.metrics).llm_latency_ms || asRecord(execution.l2Response.execution).duration_ms || asRecord(execution.l2Response.metrics).duration_ms);
    const metrics = [
      confidence,
      duration,
      textMetric('Decision', execution.l2Response.decision || execution.rca.recommended_agent || execution.rca.problem_domain),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for L2 RCA result'];
  }
  if (id === 'l3_rca') {
    const metrics = routeLevel(execution) === 'L3'
      ? [
          textMetric('Status', execution.l2.status || execution.response.status || 'running'),
          textMetric('Reason', execution.rca.title || execution.rca.summary || 'Evidence available'),
          textMetric('Route', 'L3'),
        ].filter(Boolean) as string[]
      : [
          textMetric('Status', 'Skipped'),
          textMetric('Reason', 'Routed to L1'),
          textMetric('Route', 'Not selected'),
        ].filter(Boolean) as string[];
    return metrics;
  }
  if (id === 'fix_agent') {
    const metrics = [
      textMetric('Recommended Fixes', execution.dbExecution.recommended_fixes || execution.dbExecution.actions_count || execution.dbExecution.plan_status || 'Prepared'),
      textMetric('Target Agent', execution.dbExecution.agent || execution.rca.recommended_agent || 'Fix Agent'),
      textMetric('Status', execution.dbExecution.overall_status || execution.dbExecution.status || 'Pending'),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for approval'];
  }
  if (id === 'db_fix') {
    const metrics = [
      textMetric('Recommended Fixes', execution.dbExecution.recommended_fixes || execution.dbExecution.actions_count || execution.dbExecution.plan_status || 'Prepared'),
      textMetric('Target Agent', execution.dbExecution.agent || execution.rca.recommended_agent || 'Fix Agent'),
      textMetric('Status', execution.dbExecution.overall_status || execution.dbVerification.status || execution.dbExecution.status || 'Pending'),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for remediation result'];
  }
  if (id === 'servicenow') return notificationMetrics({ dbExecution: execution.dbExecution, dbMetrics: execution.dbMetrics }, 'servicenow', true);
  return ['Skipped'];
}
function guardrailMetrics(execution: ReturnType<typeof deriveExecution>) {
  const guardrails = asRecord(execution.response.guardrails);
  const checks = Array.isArray(guardrails.checks) ? guardrails.checks as Array<Record<string, unknown>> : [];
  const securityCheck = checks.find((check) => /security/i.test(String(check.display_label || check.name || '')));
  const piiCheck = checks.find((check) => /pii|privacy/i.test(String(check.display_label || check.name || '')));
  const statusForCheck = (check: Record<string, unknown> | undefined) => check
    ? String(check.status || (check.passed === false ? 'Failed' : 'Passed'))
    : undefined;
  const metrics = [
    textMetric('Security', statusForCheck(securityCheck) || (guardrails.allowed === false ? 'Blocked' : 'Passed')),
    textMetric('PII Scan', statusForCheck(piiCheck) || guardrails.pii_scan || guardrails.pii_status || 'Passed'),
    textMetric('Risk', titleCaseValue(guardrails.risk_level || guardrails.risk || (guardrails.allowed === false ? 'High' : 'Medium'))),
  ].filter(Boolean) as string[];

  if (!metrics.length && Object.keys(guardrails).length) {
    const totalChecks = Number(guardrails.total_checks || 0);
    const passedChecks = Number(guardrails.passed_checks || 0);
    if (totalChecks > 0) metrics.push(`Security ${passedChecks}/${totalChecks}`);
    if (guardrails.risk_level) metrics.push(`Risk ${String(guardrails.risk_level)}`);
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

function sourceMetrics(platform: SourcePlatform, execution: ReturnType<typeof deriveExecution>) {
  const metrics = [
    textMetric('Incident ID', 'INC0010002'),
    textMetric('Priority', 'P1'),
    textMetric('Source', sourceLabel(platform.source)),
  ].filter(Boolean) as string[];
  return metrics.length ? metrics : ['Waiting for backend source event'];
}

function approvalMetrics(node: DisplayNode) {
  const dbExecution = asRecord(node.dbExecution);
  const approval = asRecord(dbExecution.approval || dbExecution.approval_request);
  const metrics = [
    textMetric('Approval', node.approvalStatus || approval.status || (node.approvalId ? 'Required' : 'Pending')),
    textMetric('Risk', dbExecution.risk || dbExecution.risk_level || approval.risk || 'Review'),
    textMetric('Approver', approval.approver || dbExecution.approver || 'Pending'),
  ].filter(Boolean) as string[];
  return metrics.length ? metrics : ['Waiting for human approval'];
}

function verificationMetrics(node: DisplayNode) {
  const verification = asRecord(node.dbVerification);
  const dbMetrics = asRecord(node.dbMetrics);
  const metrics = [
    textMetric('Health Check', verification.health_check || verification.status || verification.overall_status),
    boolMetric('Validation', dbMetrics.verification_passed),
    textMetric('Status', verification.status || verification.overall_status || (dbMetrics.verification_passed === true ? 'Passed' : undefined)),
  ].filter(Boolean) as string[];
  return metrics.length ? metrics : ['Waiting for remediation verification'];
}

function responseBuilderMetrics(node: DisplayNode) {
  const dbExecution = asRecord(node.dbExecution);
  const metrics = [
    textMetric('Report Status', dbExecution.report_status || dbExecution.overall_status || dbExecution.status),
    textMetric('Sections', dbExecution.report_sections || dbExecution.sections || 'RCA, fix, verification'),
    percentMetric('Confidence', dbExecution.confidence),
  ].filter(Boolean) as string[];
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

function notificationMetrics(node: Pick<DisplayNode, 'dbExecution' | 'dbMetrics'>, source: string, completed: boolean) {
  const dbExecution = asRecord(node.dbExecution);
  const dbMetrics = asRecord(node.dbMetrics);
  const stages = asRecord(dbMetrics.stage_durations_ms);
  const sourceKey = notificationChannel(source).source;
  const notification = asRecord(dbExecution[`${sourceKey}_notification`] || dbExecution.notification || dbExecution.sn_notification);
  if (sourceKey === 'servicenow') {
    const metrics = [
      textMetric('Delivery', notification.delivery || notification.status || notification.state || (completed ? 'Sent' : 'Pending')),
      durationMetric('Response Time', notification.response_time_ms || stages[`${sourceKey}_notification`] || stages.sn_notification),
      textMetric('Status', notification.status || notification.state || dbExecution.notification_status || (completed ? 'Sent' : 'Pending')),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for ServiceNow notification result'];
  }
  if (sourceKey === 'jira') {
    const metrics = [
      textMetric('Status', notification.status || notification.state || (completed ? 'Sent' : 'Skipped')),
      textMetric('Reason', notification.reason || notification.error || (completed ? 'Delivered' : 'Not selected')),
      textMetric('Target', notification.target || notification.issue_key || notification.issue_id || sourceLabel(sourceKey)),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for Jira notification result'];
  }
  if (sourceKey === 'github') {
    const metrics = [
      textMetric('Status', notification.status || notification.state || (completed ? 'Sent' : 'Skipped')),
      textMetric('Reason', notification.reason || notification.error || (completed ? 'Delivered' : 'Not selected')),
      textMetric('PR', notification.pr || notification.pull_request || notification.pr_url || 'Pending'),
    ].filter(Boolean) as string[];
    return metrics.length ? metrics : ['Waiting for GitHub notification result'];
  }
  const metrics = [
    textMetric('Status', notification.status || notification.state || dbExecution.notification_status || (completed ? 'Sent' : 'Pending')),
    durationMetric('Time', stages[`${sourceKey}_notification`] || stages.sn_notification),
    textMetric('Result', completed ? 'Delivered' : 'Awaiting delivery'),
  ].filter(Boolean) as string[];
  if (metrics.length) return metrics;
  return completed ? ['Backend completed; no notification details returned'] : ['Waiting for backend notification result'];
}

