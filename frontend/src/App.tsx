import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactFlow, { Background, Controls } from 'reactflow';
import {
  Alert,
  AppBar,
  Box,
  Button,
  Chip,
  Divider,
  Drawer,
  Grid,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import DownloadIcon from '@mui/icons-material/Download';
import { motion } from 'framer-motion';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts';
import { getLatestExecution, getWorkflow } from './api';
import { asRecord, buildFlow, chartRows, deriveExecution, toLogLines } from './derive';

export function App() {
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [paused, setPaused] = useState(false);
  const [cleared, setCleared] = useState(false);

  const workflow = useQuery({ queryKey: ['workflow'], queryFn: getWorkflow, refetchInterval: false });
  const latest = useQuery({
    queryKey: ['latest-execution'],
    queryFn: getLatestExecution,
    refetchInterval: paused ? false : 2500,
  });

  const rawResponse = latest.data?.execution?.response ?? {};
  const execution = useMemo(() => deriveExecution(rawResponse), [rawResponse]);
  const flow = useMemo(() => {
    if (!workflow.data) return { nodes: [], edges: [] };
    return buildFlow(workflow.data, rawResponse);
  }, [workflow.data, rawResponse]);
  const selectedData = selected ? flow.nodes.find((node) => node.id === selected)?.data : null;
  const logs = cleared ? [] : toLogLines(rawResponse);
  const durationRows = chartRows(execution.dbMetrics);

  return (
    <Box className="app-shell">
      <AppBar position="static" color="transparent" elevation={0} className="topbar">
        <Toolbar>
          <Typography variant="h6" sx={{ flex: 1 }}>Enterprise AI Operations Dashboard</Typography>
          <Chip label={latest.data?.status === 'ok' ? 'Live data' : 'Waiting for execution'} color={latest.data?.status === 'ok' ? 'success' : 'warning'} size="small" />
          <Tooltip title="Refresh">
            <IconButton color="primary" onClick={() => latest.refetch()}>
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>

      <Box className="content">
        {latest.isFetching && <LinearProgress className="loader" />}
        {latest.data?.status === 'empty' && (
          <Alert severity="info" sx={{ mb: 2 }}>
            No execution recorded yet. Run a real incident through the gateway, then this dashboard will populate automatically.
          </Alert>
        )}

        <Grid container spacing={2}>
          <Grid item xs={12}>
            <Panel title="Execution Workflow">
              <Box className="flow-wrap">
                <ReactFlow
                  nodes={flow.nodes}
                  edges={flow.edges}
                  fitView
                  onNodeClick={(_, node) => setSelected(node.id)}
                >
                  <Background />
                  <Controls />
                </ReactFlow>
              </Box>
            </Panel>
          </Grid>
          <Grid item xs={12} lg={5}>
            <Panel title="Incident">
              <InfoGrid data={execution.incident} />
            </Panel>
          </Grid>
          <Grid item xs={12} lg={7}>
            <Panel title="Execution Terminal">
              <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                <Button size="small" startIcon={paused ? <PlayArrowIcon /> : <PauseIcon />} onClick={() => setPaused(!paused)}>
                  {paused ? 'Resume' : 'Pause'}
                </Button>
                <Button size="small" startIcon={<ContentCopyIcon />} onClick={() => navigator.clipboard.writeText(logs.join('\n'))}>Copy</Button>
                <Button size="small" startIcon={<DeleteOutlineIcon />} onClick={() => setCleared(true)}>Clear</Button>
              </Stack>
              <Box className="terminal">
                {logs.map((line) => <div key={line}>{line}</div>)}
              </Box>
            </Panel>
          </Grid>

          <Grid item xs={12}>
            <Panel title="Observability">
              <Grid container spacing={2}>
                <Grid item xs={12} lg={6}>
                  <Typography variant="subtitle1" sx={{ mb: 1 }}>Overall Observability</Typography>
                  <MetricList metrics={execution.overallObservability} />
                  {durationRows.length > 0 && (
                    <Box className="chart">
                      <ResponsiveContainer>
                        <BarChart data={durationRows}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#263241" />
                          <XAxis dataKey="name" stroke="#9fb0c3" tick={{ fontSize: 11 }} />
                          <YAxis stroke="#9fb0c3" tick={{ fontSize: 11 }} />
                          <ChartTooltip />
                          <Bar dataKey="ms" fill="#5cc8ff" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </Box>
                  )}
                </Grid>
                <Grid item xs={12} lg={6}>
                  <Typography variant="subtitle1" sx={{ mb: 1 }}>LLM Observability</Typography>
                  <MetricList metrics={execution.llmObservability} emptyText="No LLM metrics were returned for this execution." />
                </Grid>
              </Grid>
            </Panel>
          </Grid>

          <Grid item xs={12}>
            <Panel title="Root Cause Analysis Report">
              <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
                <Button size="small" startIcon={<DownloadIcon />} onClick={() => downloadReport(execution.rcaReport, 'json')}>Download JSON</Button>
                <Button size="small" startIcon={<DownloadIcon />} onClick={() => printReport(execution.rcaReport)}>Download PDF</Button>
              </Stack>
              <ReportView report={execution.rcaReport} />
              <Divider sx={{ my: 2 }} />
              <Tabs value={tab} onChange={(_, value) => setTab(value)}>
                <Tab label="Gateway Response" />
                <Tab label="L2 Response" />
                <Tab label="DB Fix Response" />
              </Tabs>
              <Divider sx={{ mb: 2 }} />
              <JsonBlock value={tab === 0 ? rawResponse : tab === 1 ? execution.l2Response : execution.dbExecution} />
            </Panel>
          </Grid>
        </Grid>
      </Box>

      <Drawer anchor="right" open={Boolean(selected)} onClose={() => setSelected(null)}>
        <Box className="drawer">
          <Typography variant="h6">{selectedData?.label}</Typography>
          <Typography color="text.secondary" sx={{ mb: 2 }}>{selectedData?.service}</Typography>
          <InfoGrid data={asRecord(selectedData)} />
          <Divider sx={{ my: 2 }} />
          <Typography variant="subtitle2">Execution Data</Typography>
          <JsonBlock value={nodePayload(selected, execution)} />
        </Box>
      </Drawer>
    </Box>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Paper component={motion.section} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="panel">
      <Typography variant="h6" sx={{ mb: 1.5 }}>{title}</Typography>
      {children}
    </Paper>
  );
}

function MetricList({ metrics, emptyText = 'No metrics were returned by the backend for this section.' }: { metrics: Array<{ label: string; value: unknown; source: string }>; emptyText?: string }) {
  if (!metrics.length) {
    return <Alert severity="info">{emptyText}</Alert>;
  }

  return (
    <Grid container spacing={1}>
      {metrics.map((metric) => (
        <Metric key={`${metric.label}-${metric.source}`} label={metric.label} value={formatMetric(metric.value)} source={metric.source} />
      ))}
    </Grid>
  );
}

function Metric({ label, value, source }: { label: string; value: unknown; source: string }) {
  return (
    <Grid item xs={12} sm={6}>
      <Box className="metric">
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h6">{String(value ?? 'N/A')}</Typography>
        <Chip className="metric-source" label={source} size="small" />
      </Box>
    </Grid>
  );
}

function InfoGrid({ data }: { data: Record<string, unknown> }) {
  return (
    <Box className="info-grid">
      {Object.entries(data).filter(([, value]) => value !== undefined && value !== '').map(([key, value]) => (
        <Box key={key}>
          <Typography variant="caption" color="text.secondary">{key.replace(/([A-Z])/g, ' $1')}</Typography>
          <Typography>{String(value)}</Typography>
        </Box>
      ))}
    </Box>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value || {}, null, 2)}</pre>;
}

function ReportView({ report }: { report: Record<string, unknown> }) {
  return (
    <Box className="report-grid">
      {Object.entries(report).filter(([, value]) => value !== undefined && value !== null && value !== '').map(([key, value]) => (
        <Box key={key} className="report-section">
          <Typography variant="subtitle2">{key.replace(/([A-Z])/g, ' $1')}</Typography>
          <JsonBlock value={value} />
        </Box>
      ))}
    </Box>
  );
}

function formatMetric(value: unknown) {
  if (typeof value === 'number') return Number.isInteger(value) ? value : Math.round(value * 100) / 100;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return value;
}

function downloadReport(report: unknown, type: 'json') {
  const json = JSON.stringify(report || {}, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `rca-report.${type}`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function printReport(report: unknown) {
  const popup = window.open('', '_blank', 'noopener,noreferrer,width=900,height=700');
  if (!popup) return;
  const json = JSON.stringify(report || {}, null, 2);
  popup.document.write(`
    <html>
      <head>
        <title>RCA Report</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 32px; color: #111827; }
          h1 { font-size: 22px; margin-bottom: 16px; }
          pre { white-space: pre-wrap; font-size: 12px; line-height: 1.5; }
        </style>
      </head>
      <body>
        <h1>Enterprise AI Operations RCA Report</h1>
        <pre>${escapeHtml(json)}</pre>
      </body>
    </html>
  `);
  popup.document.close();
  popup.focus();
  popup.print();
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function nodePayload(nodeId: string | null, execution: ReturnType<typeof deriveExecution>) {
  if (nodeId === 'connector' || nodeId === 'normalizer') return execution.normalised;
  if (nodeId === 'categorization') return execution.categorisation;
  if (nodeId === 'l2_rca') return execution.l2Response;
  if (nodeId === 'db_fix' || nodeId === 'servicenow') return execution.dbExecution;
  return {};
}
