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
  node: {
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
  };
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
            status={selectedApprovalId ? 'running' : 'pending'}
            icon={<SettingsSuggestOutlinedIcon />}
            metrics={['Confidence 92.45%', 'Time 3m 12s']}
            onClick={latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined}
          />
          <BranchRcaCard
            label="L3"
            title="L3 RCA Agent"
            subtitle="High Complexity"
            status="skipped"
            icon={<CodeOutlinedIcon />}
            metrics={['Confidence 97.00%', 'Time 0s']}
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
    const waiting = Boolean(selectedApprovalId);
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
          onClickApproval={waiting ? () => onSelectApproval(selectedApprovalId) : latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined}
          actionLabel={waiting ? 'Waiting' : undefined}
        />
        <ApprovalGateCard approvalId={latestApprovalId} onOpen={latestApprovalId ? () => onSelectApproval(latestApprovalId) : undefined} />
        <Box className="connector-line connector-wide" />
        <WorkflowCard
          index={node.index + 1}
          title="Verification"
          subtitle="Verify remediation & database health"
          icon={<CheckCircleOutlineIcon />}
          status="pending"
          metrics={['Health Before Failed', 'Health After Healthy']}
          accent="green"
        />
        <WorkflowCard
          index={node.index + 2}
          title="Response Builder"
          subtitle="Prepare RCA & remediation summary"
          icon={<DescriptionOutlinedIcon />}
          status="pending"
          metrics={['Summary Size 1.2 MB', 'Time 1m 10s']}
          accent="purple"
        />
        <Box className="bottom-arrow-column">
          <span className="bottom-arrow" />
          <span className="bottom-arrow" />
        </Box>
        <BottomNotificationRow />
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

function ApprovalGateCard({ approvalId, onOpen }: { approvalId: string; onOpen?: () => void }) {
  return (
    <Box className={`approval-gate ${onOpen ? 'clickable' : ''}`} onClick={onOpen}>
      <Box className="approval-gate-card">
        <Box className="approval-gate-icon"><SecurityOutlinedIcon /></Box>
        <Box>
          <Typography className="workflow-card-title">Human Approval</Typography>
          <Typography className="workflow-card-subtitle">
            {approvalId ? `Remediation ${approvalId.slice(0, 8)} awaiting approval` : 'Waiting for a remediation plan'}
          </Typography>
        </Box>
        <StatusDot status={approvalId ? 'running' : 'pending'} label={approvalId ? 'Awaiting' : 'Pending'} />
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

function BottomNotificationRow() {
  return (
    <Box className="bottom-notifications">
      <WorkflowCard
        index={10}
        title="ServiceNow Notification"
        subtitle="Update incident with resolution"
        icon={<PaperPlaneOutlinedIcon />}
        status="pending"
        metrics={['Success Rate 98.00%', 'Time 45s']}
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

function buildDisplayNodes(
  nodes: WorkflowNode[],
  execution: ReturnType<typeof deriveExecution>,
  latestApproval?: RemediationPlan,
  selectedApproval?: RemediationPlan,
) {
  const statusById = new Map<string, NodeStatus>();
  const approvalStatus = latestApproval?.status || selectedApproval?.status || '';
  const approvalNodeId = latestApproval?.agent_type === 'db_fix' || selectedApproval?.agent_type === 'db_fix' ? 'db_fix' : undefined;

  const hasExecution = Boolean(Object.keys(execution.response).length);
  statusById.set('connector', hasExecution ? 'completed' : 'pending');
  statusById.set('normalizer', hasExecution ? 'completed' : 'pending');
  statusById.set('guardrails', hasExecution ? 'completed' : 'pending');
  statusById.set('security_guardrails', hasExecution ? 'completed' : 'pending');
  statusById.set('categorization', hasExecution ? 'completed' : 'pending');
  statusById.set('l2_rca', execution.l2.status === 'completed' ? 'running' : hasExecution ? 'completed' : 'pending');
  statusById.set('l3_rca', execution.response.status === 'codefix_waiting_for_approval' ? 'running' : 'skipped');
  statusById.set('fix_agent', execution.response.status === 'codefix_waiting_for_approval' ? 'running' : 'pending');
  statusById.set('db_fix', approvalNodeId ? (approvalStatus === 'WAITING_FOR_APPROVAL' ? 'running' : approvalStatus === 'EXECUTED' ? 'completed' : 'pending') : (hasExecution ? 'running' : 'pending'));
  statusById.set('servicenow', hasExecution ? 'pending' : 'pending');
  statusById.set('jira', 'skipped');
  statusById.set('github', 'skipped');

  const config: Array<{
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
  }> = nodes.map((node, index) => {
    const id = node.id === 'security_guardrails' ? 'guardrails' : node.id;
    const status = statusById.get(id) ?? 'pending';
    return {
      id,
      index: index + 2,
      status,
      title: id === 'guardrails' ? 'AI Guardrails' : node.label,
      subtitle: id === 'guardrails' ? 'Security & Policy Validation' : node.service,
      icon: iconForNode(id),
      metrics: metricsForNode(id, execution),
      accent: accentForNode(id),
      downstream: index < nodes.length - 1,
      approvalId: id === 'db_fix' ? latestApproval?.approval_id ?? selectedApproval?.approval_id ?? '' : '',
    };
  });

  return config.filter((node) => ['connector', 'normalizer', 'guardrails', 'categorization', 'db_fix'].includes(node.id));
}

function metricsForNode(id: string, execution: ReturnType<typeof deriveExecution>) {
  if (id === 'connector') return ['Success Rate 99.8%', 'Time 1m 04s', 'Records 0'];
  if (id === 'normalizer') return ['Success Rate 99.7%', 'Time 1m 21s', 'Records 0'];
  if (id === 'guardrails') {
    const guardrails = asRecord(execution.response.guardrails);
    const checks = Array.isArray(guardrails.checks) ? guardrails.checks as Array<Record<string, unknown>> : [];
    const policyPassed = checks.length ? checks.every((item) => item.passed !== false) : guardrails.allowed !== false;
    return ['PII Scan Passed', `Policy Check ${policyPassed ? 'Passed' : 'Failed'}`, `Confidence ${String(guardrails.confidence || '96%')}`];
  }
  if (id === 'categorization') return ['Confidence 95.12%', 'Time 2m 05s', 'Route L2'];
  if (id === 'l2_rca') return ['Confidence 92.45%', 'Time 3m 12s'];
  if (id === 'l3_rca') return ['Confidence 97.00%', 'Time 0s'];
  if (id === 'fix_agent') return ['Waiting for approval', 'Remediation plan prepared'];
  if (id === 'db_fix') return ['Issues Found 5', 'Actions Executed 5', 'Time 4m 35s'];
  if (id === 'servicenow') return ['Success Rate 98.00%', 'Time 45s'];
  return ['Skipped'];
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
