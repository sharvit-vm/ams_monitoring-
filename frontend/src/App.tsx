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
import { approveRemediation, getLatestExecution, getPendingApprovals, getWorkflow, rejectRemediation, type RemediationPlan, type WorkflowNode } from './api';
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
  const selectedPlatform = sourcePlatforms.find((platform) => platform.source === selectedSource) ?? sourcePlatforms[0];
  const sourceActionLabel = selectedSource === 'servicenow' ? 'Create Incident' : 'Create Issue';
  const updatedAt = latest.data?.execution?.recorded_at;

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
                <MenuItem value="servicenow">ServiceNow</MenuItem>
                <MenuItem value="jira">Jira</MenuItem>
                <MenuItem value="github">GitHub</MenuItem>
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
            <SummaryStat label="Total Steps" value={activeNodes.length} />
            <SummaryStat label="Completed" value={activeNodes.filter((node) => node.status === 'completed').length} />
            <SummaryStat label="In Progress" value={activeNodes.filter((node) => node.status === 'running').length} />
            <SummaryStat label="Pending" value={activeNodes.filter((node) => node.status === 'pending').length} />
            <SummaryStat label="Skipped" value={activeNodes.filter((node) => node.status === 'skipped').length} />
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
                icon={<Box className="sn-badge">SN</Box>}
                status="completed"
                metrics={['Success Rate 99.9%', 'Time 2m 14s', 'Records 0']}
                accent="green"
                onClickApproval={() => openSourceCreatePage(selectedPlatform)}
                actionLabel="Open"
              />
            )}

            {activeNodes.map((node) => (
              <NodeBlock
                key={node.id}
                node={node}
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
          {metrics.map((metric) => <span key={metric}>{metric}</span>)}
        </Box>
      </Box>
    </Box>
  );
}

function StatusDot({ status, label }: { status: NodeStatus; label?: string }) {
  const text = label || (status === 'completed' ? 'Done' : status === 'running' ? 'In Progress' : status === 'skipped' ? 'Skipped' : 'Pending');
  return <span className={`status-pill ${status}`}>{text}</span>;
}

function SummaryStat({ label, value }: { label: string; value: number }) {
  return (
    <Box className="summary-stat">
      <span>{label}</span>
      <b>{value}</b>
    </Box>
  );
}

function NodeBlock({
  node,
  selectedApprovalId,
  onSelectApproval,
  latestApprovalId,
}: {
  node: DisplayNode;
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
            title="L2 RCA Agent"
            subtitle="Medium Complexity"
            status={node.l2Status ?? 'pending'}
            icon={<SettingsSuggestOutlinedIcon />}
            metrics={node.l2Metrics ?? ['Waiting for L2 RCA result']}
            onClick={latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined}
          />
          <BranchRcaCard
            label="L3"
            title="L3 RCA Agent"
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
        <ApprovalGateCard approvalId={latestApprovalId} onOpen={latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined} status={waiting ? 'running' : followupStatus} />
        <Box className="connector-line connector-wide" />
        <WorkflowCard
          index={node.index + 1}
          title="Verification"
          subtitle="Verify remediation & database health"
          icon={<CheckCircleOutlineIcon />}
          status={followupStatus}
          metrics={followupStatus === 'completed' ? ['Verification Passed', 'Health After Healthy'] : ['Waiting for remediation result']}
          accent="green"
        />
        <WorkflowCard
          index={node.index + 2}
          title="Response Builder"
          subtitle="Prepare RCA & remediation summary"
          icon={<DescriptionOutlinedIcon />}
          status={followupStatus}
          metrics={followupStatus === 'completed' ? ['RCA summary prepared', 'Incident updated'] : ['Waiting for remediation result']}
          accent="purple"
        />
        <Box className="bottom-arrow-column">
          <span className="bottom-arrow" />
          <span className="bottom-arrow" />
        </Box>
        <BottomNotificationRow completed={followupStatus === 'completed'} />
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
            {props.metrics.map((metric) => <span key={metric}>{metric}</span>)}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

function ApprovalGateCard({ approvalId, onOpen, status = approvalId ? 'running' : 'pending' }: { approvalId: string; onOpen?: () => void; status?: NodeStatus }) {
  return (
    <Box className={`approval-gate ${onOpen ? 'clickable' : ''}`} onClick={onOpen}>
      <Box className="approval-gate-card">
        <Box className="approval-gate-icon"><SecurityOutlinedIcon /></Box>
        <Box>
          <Typography className="workflow-card-title">Human Approval</Typography>
          <Typography className="workflow-card-subtitle">
            {status === 'completed' ? 'Approval completed and remediation executed' : approvalId ? `Remediation ${approvalId.slice(0, 8)} awaiting approval` : 'Waiting for a remediation plan'}
          </Typography>
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

function BottomNotificationRow({ completed = false }: { completed?: boolean }) {
  return (
    <Box className="bottom-notifications">
      <WorkflowCard
        index={10}
        title="ServiceNow Notification"
        subtitle="Update incident with resolution"
        icon={<PaperPlaneOutlinedIcon />}
        status={completed ? 'completed' : 'pending'}
        metrics={completed ? ['Incident updated', 'State Resolved'] : ['Waiting for response']}
        accent="green"
        compact
      />
      <Box className="notification-branches">
        <WorkflowCard
          index={11}
          title="Jira Notification"
          subtitle="Skipped (Not applicable for L2 path)"
          icon={<BugReportOutlinedIcon />}
          status="skipped"
          metrics={['Skipped']}
          accent="blue"
          compact
        />
        <WorkflowCard
          index={12}
          title="GitHub Notification"
          subtitle="Skipped (Not applicable for L2 path)"
          icon={<CodeOutlinedIcon />}
          status="skipped"
          metrics={['Skipped']}
          accent="purple"
          compact
        />
      </Box>
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
      followupStatus,
    };
  });

  return config.filter((node) => ['connector', 'normalizer', 'guardrails', 'categorization', 'db_fix'].includes(node.id));
}

function metricsForNode(id: string, execution: ReturnType<typeof deriveExecution>) {
  if (id === 'connector') return ['Success Rate 99.8%', 'Time 1m 04s', 'Records 0'];
  if (id === 'normalizer') return ['Success Rate 99.7%', 'Time 1m 21s', 'Records 0'];
  if (id === 'guardrails') return guardrailMetrics(execution);
  if (id === 'categorization') {
    const confidence = percentMetric('Confidence', execution.categorisation.confidence);
    const level = routeLevel(execution);
    return [confidence, level ? `Route ${level}` : undefined, execution.categorisation.category ? `Category ${execution.categorisation.category}` : undefined].filter(Boolean) as string[];
  }
  if (id === 'l2_rca') {
    const confidence = percentMetric('Confidence', execution.l2Response.confidence || execution.rca.confidence);
    return [confidence, execution.rca.recommended_agent ? `Agent ${execution.rca.recommended_agent}` : undefined, execution.l2.status ? `Status ${execution.l2.status}` : undefined].filter(Boolean) as string[];
  }
  if (id === 'l3_rca') return routeLevel(execution) === 'L3' ? ['L3 route selected'] : ['Skipped'];
  if (id === 'fix_agent') return ['Waiting for approval', 'Remediation plan prepared'];
  if (id === 'db_fix') {
    const actions = execution.dbMetrics.actions_executed ?? execution.dbExecution.actions_executed;
    const finalStatus = execution.dbExecution.overall_status || execution.dbVerification.status || execution.dbExecution.status;
    const confidence = percentMetric('Confidence', execution.dbExecution.confidence);
    return [actions !== undefined ? `Actions Executed ${actions}` : undefined, finalStatus ? `Status ${finalStatus}` : undefined, confidence].filter(Boolean) as string[];
  }
  if (id === 'servicenow') return ['Success Rate 98.00%', 'Time 45s'];
  return ['Skipped'];
}
function guardrailMetrics(execution: ReturnType<typeof deriveExecution>) {
  const guardrails = asRecord(execution.response.guardrails);
  const checks = Array.isArray(guardrails.checks) ? guardrails.checks as Array<Record<string, unknown>> : [];
  const metrics = checks.slice(0, 2).map((check) => {
    const label = String(check.display_label || check.name || '').replace(/_/g, ' ').trim();
    const status = String(check.status || (check.passed === false ? 'Failed' : 'Passed'));
    return `${label} ${status}`.trim();
  }).filter(Boolean);

  if (typeof guardrails.confidence === 'number') {
    metrics.push(`Confidence ${Math.round(guardrails.confidence * 100)}%`);
  }

  if (!metrics.length && Object.keys(guardrails).length) {
    const totalChecks = Number(guardrails.total_checks || 0);
    const passedChecks = Number(guardrails.passed_checks || 0);
    if (totalChecks > 0) metrics.push(`Checks ${passedChecks}/${totalChecks}`);
    if (guardrails.risk_level) metrics.push(`Risk ${String(guardrails.risk_level)}`);
  }

  return metrics.length ? metrics : ['Waiting for backend guardrail result'];
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

