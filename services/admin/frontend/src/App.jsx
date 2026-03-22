import React, { useEffect, useMemo, useState } from 'react';
import {
  BrowserRouter as Router,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom';
import LandingPage from './features/landing/LandingPage';
import EnvironmentsPage from './features/environments/EnvironmentsPage';
import EnvironmentDetailPage from './features/environments/EnvironmentDetailPage';
import RunsPage from './features/runs/RunsPage';
import RunDashboard from './features/runs/RunDashboard';
import { AppSidebar } from '@/registry/new-york-v4/blocks/sidebar-16/components/app-sidebar';
import { SiteHeader } from '@/registry/new-york-v4/blocks/sidebar-16/components/site-header';
import { ConfirmDialogHost } from './components/ui/confirm-dialog-host';
import {
  SidebarInset,
  SidebarProvider,
} from './components/ui/sidebar';
import { Toaster } from './components/ui/sonner';
import { TooltipProvider } from './components/ui/tooltip';

const THEME_STORAGE_KEY = 'mase-admin-theme';

function readStorageValue(key, fallback) {
  if (typeof window === 'undefined') return fallback;
  const stored = window.localStorage.getItem(key);
  return stored ?? fallback;
}

function decodeSegment(segment) {
  if (!segment) return '';
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function buildCrumbs(pathname) {
  if (pathname === '/runs') {
    return [{ label: 'Runs' }];
  }
  if (/^\/runs\/[^/]+\/(stats|traces)(?:\/[^/]+)?$/.test(pathname)) {
    const [, , rawRunId] = pathname.split('/');
    return [{ label: 'Runs', to: '/runs' }, { label: decodeSegment(rawRunId) }];
  }
  if (pathname === '/environments') {
    return [{ label: 'Environments' }];
  }
  if (pathname.startsWith('/environments/')) {
    const [, , rawEnvironmentId] = pathname.split('/');
    return [{ label: 'Environments', to: '/environments' }, { label: decodeSegment(rawEnvironmentId) }];
  }
  return [{ label: 'Runs', to: '/runs' }];
}

function RunRedirect({ mode }) {
  const { run_id, trace_id } = useParams();
  const location = useLocation();
  if (!run_id) {
    return <Navigate to={`/runs${location.search}`} replace />;
  }
  const normalizedMode = String(mode || '').toLowerCase();
  const runMode = normalizedMode === 'traces'
      ? 'traces'
      : 'stats';
  const query = location.search;
  const detailSuffix = runMode === 'traces' && trace_id ? `/${encodeURIComponent(trace_id)}` : '';
  return (
    <Navigate
      to={`/runs/${encodeURIComponent(run_id)}/${runMode}${detailSuffix}${query}`}
      replace
    />
  );
}

function Shell() {
  const location = useLocation();
  const isRunObservabilityWorkspace = /\/runs\/[^/]+\/(stats|traces)(?:\/[^/]+)?$/.test(location.pathname);
  const [theme, setTheme] = useState(() => {
    const stored = readStorageValue(THEME_STORAGE_KEY, 'light');
    return stored === 'dark' ? 'dark' : 'light';
  });

  const crumbSpec = useMemo(() => buildCrumbs(location.pathname), [location.pathname]);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.style.colorScheme = theme === 'dark' ? 'dark' : 'light';
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);

    const themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) {
      themeMeta.setAttribute('content', theme === 'dark' ? '#0b0b0f' : '#ffffff');
    }
  }, [theme]);

  const toggleTheme = () => setTheme((v) => (v === 'dark' ? 'light' : 'dark'));

  return (
    <TooltipProvider>
      <SidebarProvider className="flex h-screen w-full overflow-hidden">
        <AppSidebar />
        <SidebarInset className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <SiteHeader breadcrumbs={crumbSpec} theme={theme} onThemeToggle={toggleTheme} />
          <main className="flex-1 overflow-y-auto overscroll-contain">
            <section
              className={
                isRunObservabilityWorkspace
                  ? 'w-full p-0'
                  : 'mx-auto w-full max-w-7xl p-4 md:p-6 lg:p-8'
              }
            >
              <Routes>
                <Route path="/environments" element={<EnvironmentsPage />} />
                <Route path="/environments/:environment_id" element={<EnvironmentDetailPage />} />
                <Route path="/runs" element={<RunsPage />} />
                <Route path="/runs/:run_id" element={<RunRedirect mode="stats" />} />
                <Route path="/runs/:run_id/stats" element={<RunDashboard mode="stats" />} />
                <Route path="/runs/:run_id/traces" element={<RunDashboard mode="traces" />} />
                <Route path="/runs/:run_id/traces/:trace_id" element={<RunDashboard mode="traces" />} />

                <Route path="*" element={<Navigate to="/runs" replace />} />
              </Routes>
            </section>
          </main>
        </SidebarInset>
      </SidebarProvider>
      <ConfirmDialogHost />
      <Toaster richColors closeButton />
    </TooltipProvider>
  );
}

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/*" element={<Shell />} />
      </Routes>
    </Router>
  );
}

export default App;
