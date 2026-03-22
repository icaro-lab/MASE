import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import App from './App';

vi.mock('./features/landing/LandingPage', () => ({
  default: () => <div>landing-page</div>,
}));

vi.mock('./features/environments/EnvironmentsPage', () => ({
  default: () => <div>environments-page</div>,
}));

vi.mock('./features/environments/EnvironmentDetailPage', () => ({
  default: () => <div>environment-detail-page</div>,
}));

vi.mock('./features/runs/RunsPage', () => ({
  default: () => <div>runs-page</div>,
}));

vi.mock('./features/runs/RunDashboard', () => ({
  default: ({ mode }) => <div>{`run-dashboard-${mode}`}</div>,
}));

vi.mock('@/registry/new-york-v4/blocks/sidebar-16/components/app-sidebar', () => ({
  AppSidebar: () => <div>sidebar</div>,
}));

vi.mock('@/registry/new-york-v4/blocks/sidebar-16/components/site-header', () => ({
  SiteHeader: () => <div>site-header</div>,
}));

vi.mock('./components/ui/confirm-dialog-host', () => ({
  ConfirmDialogHost: () => null,
}));

vi.mock('./components/ui/sidebar', () => ({
  SidebarProvider: ({ children }) => <div>{children}</div>,
  SidebarInset: ({ children }) => <div>{children}</div>,
}));

vi.mock('./components/ui/sonner', () => ({
  Toaster: () => null,
}));

vi.mock('./components/ui/tooltip', () => ({
  TooltipProvider: ({ children }) => <div>{children}</div>,
}));

function renderAt(pathname) {
  window.history.pushState({}, '', pathname);
  return render(<App />);
}

describe('App routing', () => {
  it('opens the landing page at root', async () => {
    renderAt('/');

    await waitFor(() => {
      expect(screen.getByText('landing-page')).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe('/');
  });

  it('opens the canonical runs registry', async () => {
    renderAt('/runs');

    await waitFor(() => {
      expect(screen.getByText('runs-page')).toBeInTheDocument();
    });
  });

  it('redirects the short run route to canonical run stats', async () => {
    renderAt('/runs/run-1');

    await waitFor(() => {
      expect(screen.getByText('run-dashboard-stats')).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe('/runs/run-1/stats');
  });

  it('opens the canonical run trace dashboard routes', async () => {
    renderAt('/runs/run-1/traces');

    await waitFor(() => {
      expect(screen.getByText('run-dashboard-traces')).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe('/runs/run-1/traces');

    cleanup();
    renderAt('/runs/run-1/traces/trace-1');

    await waitFor(() => {
      expect(screen.getByText('run-dashboard-traces')).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe('/runs/run-1/traces/trace-1');
  });

  it('redirects unknown shell routes to runs', async () => {
    renderAt('/unknown-route');
    await waitFor(() => {
      expect(screen.getByText('runs-page')).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe('/runs');
  });
});
