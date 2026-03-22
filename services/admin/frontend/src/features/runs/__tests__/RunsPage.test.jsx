import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import RunsPage from '../RunsPage';
import { platformApi, runApi } from '@/api';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

vi.mock('sonner', () => ({
  toast: {
    success: (...args) => mockToastSuccess(...args),
    error: (...args) => mockToastError(...args),
  },
}));

vi.mock('@/api', () => ({
  platformApi: {
    listRuns: vi.fn(),
    listEnvironments: vi.fn(),
  },
  runApi: {
    pause: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(),
    delete: vi.fn(),
  },
}));

function renderPage(route = '/runs?environment_id=moltbook') {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/runs" element={<RunsPage />} />
        </Routes>
      </MemoryRouter>
    </TooltipProvider>
  );
}

describe('RunsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const snapshotHash = 'sha256:1234567890abcdefghijklmnopqrstuvwxyz';
    platformApi.listRuns.mockResolvedValue([
      {
        run_id: 'run-1',
        environment_id: 'moltbook',
        runtime_id: 'openclaw',
        agent_count: 12,
        status: 'running',
        started_at: '2026-03-20T10:00:00Z',
        ended_at: null,
        snapshot_hash: snapshotHash,
      },
    ]);
    platformApi.listEnvironments.mockResolvedValue([
      {
        id: 'moltbook',
        name: 'Moltbook',
      },
    ]);
    runApi.pause.mockResolvedValue({ status: 'paused' });
  });

  it('loads filtered runs and executes run actions through runApi', async () => {
    const user = userEvent.setup();
    renderPage();
    const fullSnapshotHash = 'sha256:1234567890abcdefghijklmnopqrstuvwxyz';
    const truncatedSnapshotHash = `${fullSnapshotHash.slice(0, 14)}...${fullSnapshotHash.slice(-6)}`;

    await screen.findByText('run-1');
    expect(platformApi.listRuns).toHaveBeenCalledWith({ environment_id: 'moltbook' });
    expect(screen.getByRole('link', { name: 'Launch run' })).toHaveAttribute('href', '/environments/moltbook');
    expect(screen.getByRole('link', { name: 'Open dashboard for run-1' })).toHaveAttribute('href', '/runs/run-1/stats');
    expect(screen.getByRole('link', { name: 'Open traces for run-1' })).toHaveAttribute('href', '/runs/run-1/traces');
    expect(screen.getByText('Run Registry')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause run-1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume run-1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop run-1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete run-1' })).toBeInTheDocument();
    expect(screen.getByText(truncatedSnapshotHash)).toBeInTheDocument();
    expect(screen.queryByText(fullSnapshotHash)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Pause run-1' }));

    await waitFor(() => {
      expect(runApi.pause).toHaveBeenCalledWith('run-1');
    });
    expect(mockToastSuccess).toHaveBeenCalledWith('Updated run run-1');
    expect(platformApi.listRuns).toHaveBeenCalledTimes(2);
  });
});
