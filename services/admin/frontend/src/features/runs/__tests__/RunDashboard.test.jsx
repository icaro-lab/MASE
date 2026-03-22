import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import RunDashboard from '../RunDashboard';

const mockController = vi.fn();

vi.mock('components/run/useRunDashboardController', () => ({
  useRunDashboardController: (...args) => mockController(...args),
}));

vi.mock('components/run/RunLivePanel', () => ({
  RunLivePanel: () => <div>live panel</div>,
}));

vi.mock('components/run/RunTracingPanel', () => ({
  RunTracingPanel: () => <div>tracing panel</div>,
}));

function baseControllerState(overrides = {}) {
  return {
    runBasePath: '/runs/run-1',
    runPathQuery: '',
    loading: false,
    run: {
      run_id: 'run-1',
      status: 'completed',
      started_at: '2026-03-21T10:00:00Z',
      ended_at: '2026-03-21T10:01:00Z',
    },
    error: '',
    runCostError: '',
    schedulerStatusError: '',
    streamIndicator: { tone: 'healthy', label: 'live' },
    schedulerIndicator: { tone: 'offline', label: 'stopped', detail: 'done' },
    refreshAll: vi.fn(),
    environmentPreviewUrl: 'http://localhost:19001',
    openEnvironmentPreview: vi.fn(),
    canOpenEnvironment: true,
    environmentActionMode: 'relaunch',
    pricingUnavailable: false,
    canPauseResume: false,
    runToggleAction: '',
    canStopRun: false,
    canDeleteRun: true,
    actionBusy: '',
    stopping: false,
    handlePauseResume: vi.fn(),
    handleStopRun: vi.fn(),
    handleDeleteRun: vi.fn(),
    confirmDialog: {
      open: false,
      title: '',
      description: '',
      confirmLabel: 'Confirm',
      destructive: false,
    },
    closeConfirmDialog: vi.fn(),
    livePanelProps: {
      eventCount: 0,
      costTotals: { total: 0 },
      agentCount: 0,
    },
    tracingPanelProps: {},
    ...overrides,
  };
}

function renderDashboard(route = '/runs/run-1/stats') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/runs/:run_id/:mode" element={<RunDashboard mode="stats" />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('RunDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders relaunch environment action for terminal runs', async () => {
    const user = userEvent.setup();
    const controller = baseControllerState();
    mockController.mockReturnValue(controller);

    renderDashboard();

    const button = screen.getByRole('button', { name: 'Relaunch & open environment' });
    expect(button).toBeInTheDocument();
    await user.click(button);
    expect(controller.openEnvironmentPreview).toHaveBeenCalledTimes(1);
  });

  it('shows n/a cost header when pricing is unavailable', () => {
    mockController.mockReturnValue(
      baseControllerState({
        pricingUnavailable: true,
      })
    );

    renderDashboard();

    expect(screen.getByText('n/a')).toBeInTheDocument();
  });
});
