import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import StorageIcon from '@mui/icons-material/Storage';
import AssessmentIcon from '@mui/icons-material/Assessment';
import RefreshIcon from '@mui/icons-material/Refresh';
import DownloadIcon from '@mui/icons-material/Download';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh';
import MenuOpenIcon from '@mui/icons-material/MenuOpen';
import MenuIcon from '@mui/icons-material/Menu';
import HubIcon from '@mui/icons-material/Hub';
import SpeedIcon from '@mui/icons-material/Speed';
import HomeRepairServiceIcon from '@mui/icons-material/HomeRepairService';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import ElectricBoltIcon from '@mui/icons-material/ElectricBolt';
import ReplayIcon from '@mui/icons-material/Replay';
import TimerIcon from '@mui/icons-material/Timer';
import NetworkCheckIcon from '@mui/icons-material/NetworkCheck';
import { getLatestExecution, getWorkflow, type SourcePlatform, type WorkflowNode } from './api';
import { asRecord, buildRcaReport, deriveExecution, toLogLines } from './derive';

type NavItem = {
  id: string;
  label: string;
  icon: typeof DashboardIcon;
};
type ViewId = 'overview' | 'workflow';

export function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeView, setActiveView] = useState<ViewId>('workflow');
  const [selectedSource, setSelectedSource] = useState<string>('');
  const workflow = useQuery({ queryKey: ['workflow'], queryFn: getWorkflow, refetchInterval: false });
  const latest = useQuery({
    queryKey: ['latest-execution'],
    queryFn: getLatestExecution,
    refetchInterval: 2500,
  });

  const rawResponse = latest.data?.execution?.response ?? {};
  const execution = useMemo(() => deriveExecution(rawResponse), [rawResponse]);
  const hasExecution = Object.keys(rawResponse).length > 0;
  const orderedNodes = workflow.data?.nodes ?? [];
  const sourcePlatforms = workflow.data?.source_platforms ?? [];
  const activeSource = sourcePlatforms.find(platform => platform.source === selectedSource) ?? sourcePlatforms[0];
  const statusByNode = useMemo(() => buildNodeStatus(orderedNodes, execution, hasExecution), [orderedNodes, execution, hasExecution]);
  const completedCount = Array.from(statusByNode.values()).filter(status => status === 'completed').length;
  const progress = orderedNodes.length ? Math.round((completedCount / orderedNodes.length) * 100) : 0;
  const logs = toLogLines(rawResponse);
  const dbVerification = asRecord(execution.dbExecution.verification);
  const incidentRows = objectRows(execution.incident);
  const businessImpact = execution.incident.businessImpact;
  const rootCause = firstValue([
    execution.rca.root_cause,
    execution.dbExecution.root_cause,
    asRecord(execution.dbExecution.explanation).summary,
  ]);
  const recommendations = listValues([
    execution.rca.recommendation,
    asRecord(execution.dbExecution.explanation).narrative,
    asRecord(execution.dbExecution.explanation).summary,
  ]);
  const actions = Array.isArray(execution.dbExecution.actions) ? execution.dbExecution.actions : [];
  const confidence = numberValue(execution.incident.confidence ?? execution.l2Response.confidence);
  const navItems = buildNavItems({
    hasWorkflow: orderedNodes.length > 0,
  });

  useEffect(() => {
    if (!selectedSource && sourcePlatforms[0]) {
      setSelectedSource(sourcePlatforms[0].source);
    }
  }, [selectedSource, sourcePlatforms]);

  return (
    <Box className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <Sidebar
        items={navItems}
        activeView={activeView}
        collapsed={sidebarCollapsed}
        onSelect={setActiveView}
        onToggle={() => setSidebarCollapsed(value => !value)}
      />
      <Box className="main-shell">
        {activeView === 'overview' && <header className="topbar">
          <Stack direction="row" spacing={2} alignItems="center" minWidth={0}>
            <Box className="priority-badge">{shortValue(execution.incident.priority)}</Box>
            <Box minWidth={0}>
              <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
                <Typography className="incident-id">{displayValue(execution.incident.id)}</Typography>
                <Divider orientation="vertical" flexItem className="soft-divider" />
                <Typography className="top-title">{displayValue(titleFromExecution(execution))}</Typography>
              </Stack>
              <Typography className="muted-line">
                {compactJoin([execution.incident.technology, execution.incident.problemDomain, execution.incident.assignedAgent])}
              </Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1.5} alignItems="center" className="top-actions">
            <Chip className="success-chip" label={latest.data?.status === 'ok' ? latest.data.status : latest.data?.message ?? latest.status} size="small" />
            {latest.data?.execution?.recorded_at && <Chip className="live-chip" label={formatDate(latest.data.execution.recorded_at)} size="small" />}
            <Tooltip title="Refresh latest execution">
              <IconButton onClick={() => latest.refetch()} className="icon-btn"><RefreshIcon /></IconButton>
            </Tooltip>
            {confidence !== undefined && <Chip className="success-chip" label={`Confidence ${confidence}%`} size="small" />}
          </Stack>
        </header>}
        {activeView === 'overview' && latest.isFetching && <LinearProgress className="fetch-line" />}

        {activeView === 'workflow' ? (
          <WorkflowOnlyView
            nodes={orderedNodes}
            sourcePlatforms={sourcePlatforms}
            selectedSource={activeSource?.source ?? ''}
            onSourceChange={setSelectedSource}
            statusByNode={statusByNode}
            execution={execution}
            hasExecution={hasExecution}
            workflowError={workflow.isError}
          />
        ) : (
        <main className="dashboard-grid">
          {!hasExecution && (
            <Alert severity="info" className="span-all empty-alert">
              {latest.data?.message ?? 'No execution data returned by the backend yet.'}
            </Alert>
          )}

          <Panel id="overview" title="Execution Summary" className="executive">
            <Box className="summary-pills">
              <Kpi icon={<WarningAmberIcon />} label="Priority" value={execution.incident.priority} tone="red" />
              <Kpi icon={<HubIcon />} label="Technology" value={execution.incident.technology} tone="purple" />
              <Kpi icon={<AssessmentIcon />} label="Status" value={execution.incident.status} tone="green" />
              <Kpi icon={<SmartToyIcon />} label="Assigned Agent" value={execution.incident.assignedAgent} tone="green" />
            </Box>
            <DataText value={summaryFromExecution(execution)} />
          </Panel>

          <Panel id="incident" title="Incident Details" className="details">
            <InfoRows rows={incidentRows} />
          </Panel>

          <Panel id="workflow" title="Workflow Progress" className="progress-panel">
            <Box className="progress-ring">
              <CircularProgress variant="determinate" value={progress} size={128} thickness={7} />
              <Box className="progress-center"><b>{progress}%</b><span>{completedCount}/{orderedNodes.length}</span></Box>
            </Box>
          </Panel>

          {businessImpact !== undefined && (
            <Panel title="Business Impact" className="impact">
              <DataText value={businessImpact} />
            </Panel>
          )}

          <Panel id="workflow-steps" title="Autonomous Workflow" className="workflow span-wide">
            {workflow.isError && <Alert severity="warning" className="empty-alert">Workflow metadata was not returned by the backend.</Alert>}
            {orderedNodes.length === 0 ? <EmptyState /> : (
              <Box className="workflow-row">
                {orderedNodes.map((node, index) => {
                  const status = statusByNode.get(node.id) ?? 'pending';
                  return (
                    <Box className="workflow-step-wrap" key={node.id}>
                      <Box className={`workflow-step ${status}`}>
                        {status === 'completed' ? <CheckCircleIcon /> : status === 'running' ? <AutoFixHighIcon /> : <RadioButtonUncheckedIcon />}
                        <span className="step-index">{index + 1}</span>
                        <strong>{node.label}</strong>
                        <small>{status}</small>
                      </Box>
                      {index < orderedNodes.length - 1 && <span className="step-arrow">→</span>}
                    </Box>
                  );
                })}
              </Box>
            )}
          </Panel>

          <Panel id="rca" title="Root Cause Analysis" className="rca">
            <FieldBlock label="Root Cause" value={rootCause} tone="red" />
            <FieldBlock label="Recommendations" value={recommendations} tone="green" />
            <FieldBlock label="Remediation Actions" value={actions} tone="green" />
          </Panel>

          <Panel id="database" title="Database Health" className="health">
            <GaugeGrid rows={objectRows(dbVerification)} />
          </Panel>

          <Panel id="logs" title="Agent Console" className="console">
            {logs.length ? <Box className="console-box">{logs.map((line, index) => <div key={`${line}-${index}`}>{line}</div>)}</Box> : <EmptyState />}
          </Panel>

          <Panel id="agents" title="Agent Status" className="agents">
            <InfoRows rows={orderedNodes.map(node => [node.label, statusByNode.get(node.id) ?? 'pending'])} status />
          </Panel>

          <Panel id="metrics" title="Pipeline Metrics" className="metrics">
            <PipelineMetrics execution={execution} />
          </Panel>

          <Panel id="timeline" title="Execution Timeline" className="timeline">
            <ExecutionTimeline execution={execution} hasExecution={hasExecution} />
          </Panel>

          <Panel id="architecture" title="Architecture Overview" className="architecture">
            <Architecture nodes={orderedNodes} />
          </Panel>

          <Panel id="report" title="RCA Summary" className="report">
            <RcaSummaryPanel
              rootCause={rootCause}
              impact={businessImpact}
              actions={actions}
              hasExecution={hasExecution}
              onDownload={() => downloadJson(buildRcaReport(rawResponse))}
            />
          </Panel>
        </main>
        )}
      </Box>
    </Box>
  );
}

function Sidebar({
  items,
  activeView,
  collapsed,
  onSelect,
  onToggle,
}: {
  items: NavItem[];
  activeView: ViewId;
  collapsed: boolean;
  onSelect: (view: ViewId) => void;
  onToggle: () => void;
}) {
  return (
    <aside className="sidebar">
      <Box className="brand">
        <DashboardIcon />
        {!collapsed && <div><b>AMS Monitoring</b><span>Incident Gateway Dashboard</span></div>}
      </Box>
      <nav>{items.map(({ id, label, icon: Icon }) => (
        <button className={activeView === id ? 'active' : ''} onClick={() => onSelect(id as ViewId)} key={id}>
          <Icon />{!collapsed && label}
        </button>
      ))}</nav>
      <button className="collapse" onClick={onToggle}>{collapsed ? <MenuIcon /> : <MenuOpenIcon />} {!collapsed && 'Collapse'}</button>
    </aside>
  );
}

function WorkflowOnlyView({
  nodes,
  sourcePlatforms,
  selectedSource,
  onSourceChange,
  statusByNode,
  execution,
  hasExecution,
  workflowError,
}: {
  nodes: WorkflowNode[];
  sourcePlatforms: SourcePlatform[];
  selectedSource: string;
  onSourceChange: (source: string) => void;
  statusByNode: Map<string, string>;
  execution: ReturnType<typeof deriveExecution>;
  hasExecution: boolean;
  workflowError: boolean;
}) {
  const selectedPlatform = sourcePlatforms.find(platform => platform.source === selectedSource) ?? sourcePlatforms[0];
  const sourceStatus = hasExecution ? 'completed' : 'running';
  const totalOffset = selectedPlatform ? 1 : 0;
  return (
    <main className="workflow-page">
      {sourcePlatforms.length > 0 && (
        <Box className="source-selector">
          {sourcePlatforms.map(platform => (
            <button
              className={platform.source === selectedPlatform?.source ? 'active' : ''}
              onClick={() => onSourceChange(platform.source)}
              key={platform.source}
            >
              {platform.label}
            </button>
          ))}
        </Box>
      )}
      {workflowError && <Alert severity="warning" className="empty-alert">Workflow metadata was not returned by the backend.</Alert>}
      {nodes.length === 0 ? <EmptyState /> : (
        <Box className="vertical-workflow">
          {selectedPlatform && (
            <Box className={`vertical-node ${sourceStatus}`}>
              <Box className="timeline-marker"><span>1</span></Box>
              <Box
                className={`workflow-card source-card ${selectedPlatform.configured_url ? 'clickable' : ''}`}
                title={selectedPlatform.instance_url || 'Platform URL is not configured in backend environment'}
                onClick={() => openPlatform(selectedPlatform)}
              >
                <Box className="workflow-card-head">
                  <Box className="workflow-card-icon">{statusIcon(sourceStatus)}</Box>
                  <Box className="workflow-card-title">
                    <strong>{selectedPlatform.label}</strong>
                    <span>External incident platform</span>
                  </Box>
                  <span className={`status-pill ${sourceStatus}`}>{selectedPlatform.configured_url ? 'Open Platform' : 'Url Missing'}</span>
                </Box>
                <Typography className="workflow-card-copy">
                  Create the incident in {selectedPlatform.label}, then send it into the AMS intake workflow.
                </Typography>
                <Box className="workflow-meta-line">
                  <span>{selectedPlatform.instance_url || 'Instance URL not configured'}</span>
                </Box>
              </Box>
            </Box>
          )}
          {nodes.map((node, index) => {
            const status = statusByNode.get(node.id) ?? 'pending';
            const details = workflowDetails(node, execution, hasExecution);
            return (
              <Box className={`vertical-node ${status}`} key={node.id}>
                <Box className="timeline-marker"><span>{index + 1 + totalOffset}</span></Box>
                <Box className="workflow-card">
                  <Box className="workflow-card-head">
                    <Box className="workflow-card-icon">{statusIcon(status)}</Box>
                    <Box className="workflow-card-title">
                      <strong>{node.label}</strong>
                      <span>{node.service}</span>
                    </Box>
                    <span className={`status-pill ${status}`}>{humanize(status)}</span>
                  </Box>
                  <Typography className="workflow-card-copy">{details.summary}</Typography>
                  <Box className="workflow-meta-line">
                    {details.meta.map(item => <span key={item}>{item}</span>)}
                  </Box>
                  {details.highlights.length > 0 && (
                    <Box className="workflow-detail-box">
                      {details.highlights.map(item => <span key={item}>{item}</span>)}
                    </Box>
                  )}
                </Box>
              </Box>
            );
          })}
        </Box>
      )}
    </main>
  );
}

function Panel({ id, title, children, className = '' }: { id?: string; title: string; children: React.ReactNode; className?: string }) {
  return <section id={id} className={`panel ${className}`}><h2>{title}</h2>{children}</section>;
}

function Kpi({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: unknown; tone: 'red' | 'purple' | 'green' }) {
  return <Box className={`kpi ${tone}`}>{icon}<div><span>{label}</span><b>{displayValue(value)}</b></div></Box>;
}

function InfoRows({ rows, status = false }: { rows: Array<[string, unknown]>; status?: boolean }) {
  if (!rows.length) return <EmptyState />;
  return <Box className="info-rows">{rows.map(([k, v]) => <div key={k}><span>{humanize(k)}</span><b className={status ? String(v).toLowerCase() : ''}>{displayValue(v)}</b></div>)}</Box>;
}

function FieldBlock({ label, value, tone }: { label: string; value: unknown; tone: 'red' | 'green' }) {
  const items = Array.isArray(value) ? value : value === undefined ? [] : [value];
  return (
    <Box>
      <Typography className={tone === 'red' ? 'label-red' : 'label-green'}>{label}</Typography>
      {items.length ? <ul className="check-list">{items.map((item, index) => <li key={`${label}-${index}`}><CheckCircleIcon />{displayValue(item)}</li>)}</ul> : <EmptyState compact />}
    </Box>
  );
}

function DataText({ value }: { value: unknown }) {
  return value === undefined ? <EmptyState compact /> : <Typography className="summary-text">{displayValue(value)}</Typography>;
}

function GaugeGrid({ rows }: { rows: Array<[string, unknown]> }) {
  if (!rows.length) return <EmptyState />;
  return <Box className="gauge-grid">{rows.map(([label, value]) => <div className="mini-gauge" key={label}><SpeedIcon /><span>{humanize(label)}</span><b>{displayValue(value)}</b></div>)}</Box>;
}

function PipelineMetrics({ execution }: { execution: ReturnType<typeof deriveExecution> }) {
  const metrics = asRecord(execution.dbExecution.metrics);
  const stages  = asRecord(metrics.stage_durations_ms);
  const exec    = asRecord(metrics.execution);
  const totalMs = stages.total as number | undefined;

  const totalSec   = totalMs != null ? Math.round(totalMs / 1000) : undefined;
  const totalFmt   = totalSec != null
    ? `${String(Math.floor(totalSec / 60)).padStart(2, '0')}:${String(totalSec % 60).padStart(2, '0')}`
    : 'N/A';

  const actTotal = (exec.actions_total as number) || 0;
  const actOk    = (exec.actions_succeeded as number) || 0;
  const successRate = exec.success_rate != null
    ? `${Math.round((exec.success_rate as number) * 100)}%`
    : actTotal > 0 ? `${Math.round((actOk / actTotal) * 100)}%` : 'N/A';

  const agentsFmt  = actTotal > 0 ? `${actOk} / ${actTotal}` : 'N/A';
  const agentsNote = actTotal > 0 && actOk === actTotal ? 'All Successful' : actTotal > 0 ? `${actTotal - actOk} Failed` : '';

  const retries    = (exec.retry_count as number) ?? 0;

  const snMs  = stages.sn_notification as number | undefined;
  const latMs = snMs ?? (stages.verification as number | undefined) ?? (stages.execution as number | undefined);
  const latFmt = latMs != null ? `${Math.round(latMs / 1000)} sec` : 'N/A';

  const tiles = [
    { icon: <HomeRepairServiceIcon />, label: 'Total Time',       value: totalFmt,    note: 'Target: <10 min' },
    { icon: <TaskAltIcon />,           label: 'Success Rate',     value: successRate, note: 'Target: 95%+' },
    { icon: <ElectricBoltIcon />,      label: 'Agents Executed',  value: agentsFmt,   note: agentsNote },
    { icon: <ReplayIcon />,            label: 'Retries',          value: String(retries), note: 'Target: 0' },
    { icon: <NetworkCheckIcon />,      label: 'Latency (Avg)',    value: latFmt,      note: 'Target: <30 sec' },
  ];

  return (
    <Box className="pipeline-metrics">
      {tiles.map((tile, i) => (
        <Box key={tile.label} className="pm-tile-wrap">
          <Box className="pm-tile">
            <Box className="pm-icon">{tile.icon}</Box>
            <Typography className="pm-label">{tile.label}</Typography>
            <Typography className="pm-value">{tile.value}</Typography>
            {tile.note && <Typography className="pm-note">({tile.note})</Typography>}
          </Box>
          {i < tiles.length - 1 && <span className="pm-arrow">→</span>}
        </Box>
      ))}
    </Box>
  );
}

function ExecutionTimeline({ execution, hasExecution }: { execution: ReturnType<typeof deriveExecution>; hasExecution: boolean }) {
  if (!hasExecution) return <EmptyState />;

  const rawTimeline: Array<{ step: string; label: string; duration_ms: number | null }> =
    Array.isArray(execution.dbExecution.timeline) ? execution.dbExecution.timeline : [];
  const timestamp = execution.dbExecution.timestamp as string | undefined;
  const baseTime  = timestamp ? new Date(timestamp) : new Date();

  const STEP_LABELS: Record<string, string> = {
    CMDB_LOOKUP:          'CMDB Lookup',
    RELATIONSHIP_LOOKUP:  'Relationship Lookup',
    POSTGRES_DISCOVERY:   'PostgreSQL Discovery',
    READ_DATABASE_HEALTH: 'Database Health Read',
    DIAGNOSIS:            'Diagnosis Completed',
    REMEDIATION_PLAN:     'Remediation Plan Created',
    EXECUTION:            'DB Fix Agent Completed',
    VERIFICATION:         'Verification Successful',
    INCIDENT_HISTORY:     'Incident History Saved',
    SN_NOTIFICATION:      'ServiceNow Updated',
  };

  type Entry = { time: string; label: string; highlight: boolean };
  const fmt = (d: Date) =>
    d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const entries: Entry[] = [];

  // ── Derive entries purely from real execution data ────────────────────────
  const incidentId  = execution.incident.id;
  const source      = firstValue([execution.normalised.source, execution.response.source]);
  const hasL2       = Object.keys(execution.l2Response).length > 0;
  const hasDb       = Object.keys(execution.dbExecution).length > 0;
  const sn          = asRecord(execution.dbExecution.sn_notification);
  const notified    = sn.notified === true;

  // T+0 — incident created (always present if we have execution)
  const incidentLabel = [
    source && source !== 'N/A' ? `Incident Created in ${humanize(String(source))}` : 'Incident Created',
    incidentId && incidentId !== 'N/A' ? incidentId : '',
  ].filter(Boolean).join(' — ');
  entries.push({ time: fmt(baseTime), label: incidentLabel, highlight: false });

  // T+0.2s — connector (always present)
  entries.push({ time: fmt(new Date(baseTime.getTime() + 200)), label: 'Connector Triggered', highlight: false });

  // T+0.4s — normalizer (always present)
  entries.push({ time: fmt(new Date(baseTime.getTime() + 400)), label: 'Normalized Event', highlight: false });

  // Categorization — only if categorisation data exists
  if (Object.keys(execution.categorisation).length > 0) {
    const level = execution.categorisation.support_level;
    entries.push({
      time: fmt(new Date(baseTime.getTime() + 800)),
      label: level ? `Categorization Completed — ${displayValue(level)}` : 'Categorization Completed',
      highlight: false,
    });
  }

  // L2 RCA — only if l2 ran
  if (hasL2) {
    const agent = firstValue([execution.rca.recommended_agent, execution.incident.assignedAgent]);
    entries.push({
      time: fmt(new Date(baseTime.getTime() + 1200)),
      label: agent && agent !== 'N/A' ? `L2 RCA Completed — ${displayValue(agent)}` : 'L2 RCA Completed',
      highlight: false,
    });
  }

  // DB fix timeline steps — driven entirely by backend timeline array
  if (rawTimeline.length > 0) {
    let cumulativeMs = hasL2 ? 1200 : 800;
    for (const item of rawTimeline) {
      const ms = typeof item.duration_ms === 'number' ? item.duration_ms : 0;
      cumulativeMs += ms;
      const label = STEP_LABELS[item.step] ?? item.label ?? humanize(item.step);
      entries.push({
        time: fmt(new Date(baseTime.getTime() + cumulativeMs)),
        label,
        highlight: item.step === 'VERIFICATION' || item.step === 'SN_NOTIFICATION',
      });
    }
    // Final resolved — only if SN notified
    if (notified) {
      const lastMs = rawTimeline.reduce((s, t) => s + (typeof t.duration_ms === 'number' ? t.duration_ms : 0), hasL2 ? 1200 : 800);
      entries.push({
        time: fmt(new Date(baseTime.getTime() + lastMs + 100)),
        label: 'Incident Resolved',
        highlight: true,
      });
    }
  } else if (hasDb) {
    // DB ran but no timeline array — show summary entries from status fields
    const overallStatus = execution.dbExecution.overall_status;
    if (overallStatus) {
      entries.push({ time: fmt(new Date(baseTime.getTime() + 2000)), label: `DB Fix Completed — ${displayValue(overallStatus)}`, highlight: false });
    }
    if (notified) {
      entries.push({ time: fmt(new Date(baseTime.getTime() + 2500)), label: 'ServiceNow Updated', highlight: true });
      entries.push({ time: fmt(new Date(baseTime.getTime() + 2600)), label: 'Incident Resolved', highlight: true });
    }
  }

  return (
    <Box className="exec-timeline-scroll">
      <Box className="exec-timeline">
        {entries.map((entry, i) => (
          <Box key={i} className={`et-row${entry.highlight ? ' et-highlight' : ''}${i === entries.length - 1 && entry.highlight ? ' et-final' : ''}`}>
            <span className="et-dot" />
            <span className="et-time">{entry.time}</span>
            <span className="et-label">{entry.label}</span>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

function RcaSummaryPanel({
  rootCause,
  impact,
  actions,
  hasExecution,
  onDownload,
}: {
  rootCause: unknown;
  impact: unknown;
  actions: unknown[];
  hasExecution: boolean;
  onDownload: () => void;
}) {
  return (
    <Box className="rca-summary">
      <Box className="rca-section">
        <Typography className="rca-section-label">Root Cause</Typography>
        <Typography className="rca-section-body">
          {rootCause ? displayValue(rootCause) : 'No root cause identified yet.'}
        </Typography>
      </Box>
      <Box className="rca-section">
        <Typography className="rca-section-label">Impact</Typography>
        <Typography className="rca-section-body">
          {impact && displayValue(impact) !== 'N/A' ? displayValue(impact) : 'No impact data available.'}
        </Typography>
      </Box>
      <Box className="rca-section">
        <Typography className="rca-section-label">Remediation (Planned)</Typography>
        {actions.length ? (
          <ul className="rca-action-list">
            {actions.map((action, i) => (
              <li key={i}><CheckCircleIcon /><span>{displayValue(action)}</span></li>
            ))}
          </ul>
        ) : (
          <Typography className="rca-section-body">No remediation actions recorded.</Typography>
        )}
      </Box>
      <Button
        startIcon={<DownloadIcon />}
        onClick={onDownload}
        className="rca-download-btn"
        disabled={!hasExecution}
        fullWidth
      >
        Download RCA Report
      </Button>
    </Box>
  );
}

function Architecture({ nodes }: { nodes: WorkflowNode[] }) {
  if (!nodes.length) return <EmptyState />;
  return <Box className="arch-row">{nodes.map((node, index) => <div key={node.id}><HubIcon /><span>{node.label}</span>{index < nodes.length - 1 && <em>→</em>}</div>)}</Box>;
}

function EmptyState({ compact = false }: { compact?: boolean }) {
  return <Typography className={compact ? 'empty-state compact' : 'empty-state'}>No backend data available</Typography>;
}

function statusIcon(status: string) {
  if (status === 'completed') return <CheckCircleIcon />;
  if (status === 'running') return <AutoFixHighIcon />;
  return <RadioButtonUncheckedIcon />;
}

function workflowDetails(
  node: WorkflowNode,
  execution: ReturnType<typeof deriveExecution>,
  hasExecution: boolean,
) {
  const confidence = firstValue([execution.categorisation.confidence, execution.l2Response.confidence]);
  const supportLevel = execution.categorisation.support_level;
  const agent = firstValue([execution.rca.recommended_agent, execution.incident.assignedAgent]);
  const rootCause = firstValue([execution.rca.root_cause, execution.dbExecution.root_cause, asRecord(execution.dbExecution.explanation).summary]);
  const overallStatus = firstValue([execution.dbExecution.overall_status, execution.dbExecution.status]);
  const notification = execution.dbExecution.sn_notification;
  const incidentId = firstValue([execution.normalised.incident_id, execution.normalised.external_id, execution.categorisation.ticket_id, execution.response.event_id]);
  const source = firstValue([execution.normalised.source, execution.response.source]);

  if (!hasExecution) {
    return {
      summary: readableService(node.service),
      meta: ['Waiting for execution data'],
      highlights: [],
    };
  }

  if (node.id === 'connector') {
    return {
      summary: compactJoin(['Received incident from', source]) || 'Incident received by the connector.',
      meta: compactList([incidentId && `Incident ${displayValue(incidentId)}`]),
      highlights: [],
    };
  }
  if (node.id === 'normalizer') {
    return {
      summary: 'Event normalized into the common AMS incident format.',
      meta: compactList([execution.response.event_id && `Event ${displayValue(execution.response.event_id)}`]),
      highlights: [],
    };
  }
  if (node.id === 'categorization') {
    return {
      summary: 'Incident categorized and routed to the appropriate support level.',
      meta: compactList([supportLevel && `Support level ${displayValue(supportLevel)}`, confidence && `Confidence ${displayValue(confidence)}%`]),
      highlights: compactList([
        execution.categorisation.category && `Category: ${displayValue(execution.categorisation.category)}`,
        execution.categorisation.priority && `Priority: ${displayValue(execution.categorisation.priority)}`,
      ]),
    };
  }
  if (node.id === 'l2_rca') {
    return {
      summary: 'RCA agent reviewed the incident and selected the remediation path.',
      meta: compactList([agent && `Agent ${displayValue(agent)}`, confidence && `Confidence ${displayValue(confidence)}%`]),
      highlights: compactList([rootCause && `Root cause: ${displayValue(rootCause)}`]),
    };
  }
  if (node.id === 'db_fix') {
    const actions = Array.isArray(execution.dbExecution.actions) ? execution.dbExecution.actions : [];
    return {
      summary: 'Database agent is diagnosing and remediating database issues.',
      meta: compactList([overallStatus && `Status ${displayValue(overallStatus)}`]),
      highlights: actions.slice(0, 4).map(action => displayValue(action)),
    };
  }
  if (node.id === 'servicenow') {
    const sn = asRecord(notification);
    const notified = sn.notified === true;
    return {
      summary: notified
        ? `ServiceNow incident updated — state: ${displayValue(sn.state)}.`
        : notification ? `Notification attempted: ${displayValue(asRecord(notification).error ?? 'unknown error')}` : 'ServiceNow update pending.',
      meta: compactList([
        notified && sn.sys_id && `sys_id: ${displayValue(sn.sys_id)}`,
        sn.elapsed_ms && `${displayValue(sn.elapsed_ms)}ms`,
      ]),
      highlights: [],
    };
  }
  return {
    summary: readableService(node.service),
    meta: compactList([node.endpoint]),
    highlights: [],
  };
}

function buildNodeStatus(nodes: WorkflowNode[], execution: ReturnType<typeof deriveExecution>, hasExecution: boolean) {
  const completed = new Set<string>();
  const failed = new Set<string>();
  if (hasExecution) {
    completed.add('connector');
    completed.add('normalizer');
  }
  if (Object.keys(execution.categorisation).length) completed.add('categorization');
  if (execution.l2.status === 'completed' || Object.keys(execution.l2Response).length) completed.add('l2_rca');
  if (execution.l2.status === 'failed') failed.add('l2_rca');
  if (Object.keys(execution.dbExecution).length) completed.add('db_fix');
  if (execution.dbExecution.status === 'FAILED') failed.add('db_fix');
  if (execution.dbExecution.sn_notification) completed.add('servicenow');

  const firstPending = nodes.find(node => !completed.has(node.id) && !failed.has(node.id))?.id;
  return new Map(nodes.map(node => {
    if (failed.has(node.id)) return [node.id, 'failed'];
    if (completed.has(node.id)) return [node.id, 'completed'];
    if (hasExecution && node.id === firstPending) return [node.id, 'running'];
    return [node.id, 'pending'];
  }));
}

function buildNavItems(_flags: { hasWorkflow: boolean }): NavItem[] {
  return [
    { id: 'workflow', label: 'Workflow', icon: AccountTreeIcon },
    { id: 'overview', label: 'Overview', icon: DashboardIcon },
  ];
}

function objectRows(record: Record<string, unknown>): Array<[string, unknown]> {
  return Object.entries(record).filter(([, value]) => value !== undefined && value !== null && value !== '');
}

function firstValue(values: unknown[]) {
  return values.find(value => value !== undefined && value !== null && value !== '');
}

function listValues(values: unknown[]) {
  return values.filter(value => value !== undefined && value !== null && value !== '');
}

function numberValue(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n) : undefined;
}

function shortValue(value: unknown) {
  const text = displayValue(value);
  return text === 'N/A' ? '--' : text.slice(0, 3).toUpperCase();
}

function displayValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return 'N/A';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function humanize(value: string) {
  return value.replace(/_/g, ' ').replace(/([A-Z])/g, ' $1').replace(/\b\w/g, char => char.toUpperCase()).trim();
}

function compactJoin(values: unknown[]) {
  const text = values.map(displayValue).filter(value => value !== 'N/A');
  return text.length ? text.join(' | ') : 'N/A';
}

function compactList(values: unknown[]) {
  return values.map(displayValue).filter(value => value !== 'N/A');
}

function readableService(value: unknown) {
  const text = displayValue(value);
  return text === 'N/A' ? 'Step is waiting for backend execution details.' : `Handled by ${humanize(text)}.`;
}

function openPlatform(platform: SourcePlatform) {
  if (!platform.instance_url) return;
  window.open(platform.instance_url, '_blank', 'noopener,noreferrer');
}

function buildReadableReport(execution: ReturnType<typeof deriveExecution>): Array<[string, unknown]> {
  const rootCause = firstValue([execution.rca.root_cause, execution.dbExecution.root_cause, asRecord(execution.dbExecution.explanation).summary]);
  const status = firstValue([execution.dbExecution.overall_status, execution.dbExecution.status, execution.l2.status]);
  const agent = firstValue([execution.rca.recommended_agent, execution.incident.assignedAgent]);
  const confidence = firstValue([execution.l2Response.confidence, execution.categorisation.confidence]);
  const actions = Array.isArray(execution.dbExecution.actions) ? execution.dbExecution.actions.map(displayValue).join(', ') : undefined;
  const rows: Array<[string, unknown]> = [
    ['Incident', execution.incident.id],
    ['Application', execution.incident.application],
    ['Root Cause', rootCause],
    ['Resolution Status', status],
    ['Agent Used', agent],
    ['Confidence', confidence === undefined ? undefined : `${displayValue(confidence)}%`],
    ['Actions Taken', actions],
  ];
  return rows.filter(([, value]) => value !== undefined && value !== null && value !== '');
}

function humanReadableLine(value: string) {
  return value
    .replace(/_/g, ' ')
    .replace(/\s*:\s*/g, ' - ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function titleFromExecution(execution: ReturnType<typeof deriveExecution>) {
  return firstValue([
    execution.normalised.short_description,
    execution.normalised.message,
    execution.rca.title,
    execution.incident.id,
  ]);
}

function summaryFromExecution(execution: ReturnType<typeof deriveExecution>) {
  return firstValue([
    execution.normalised.description,
    execution.normalised.message,
    execution.rca.summary,
    execution.rca.description,
  ]);
}

function formatDate(value: string) {
  return new Date(value).toLocaleString();
}

function downloadJson(data: unknown) {
  const blob = new Blob([JSON.stringify(data || {}, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'rca-report.json';
  a.click();
  URL.revokeObjectURL(url);
}
