import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material';
import { App } from './App';
import './styles.css';

const queryClient = new QueryClient();

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#5cc8ff' },
    background: { default: '#0d1117', paper: '#161b22' },
  },
  typography: { fontFamily: 'Inter, system-ui, sans-serif' },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <App />
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
