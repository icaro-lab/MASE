import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import EnvironmentDetailPage from '../EnvironmentDetailPage';
import { platformApi } from '@/api';

const mockNavigate = vi.fn();
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('sonner', () => ({
  toast: {
    success: (...args) => mockToastSuccess(...args),
    error: (...args) => mockToastError(...args),
  },
}));

vi.mock('@/api', () => ({
  platformApi: {
    getEnvironment: vi.fn(),
    getRuntime: vi.fn(),
    validateEnvironment: vi.fn(),
    listRuns: vi.fn(),
    createRun: vi.fn(),
  },
}));

function renderPage(route = '/environments/moltbook') {
  return render(
    <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/environments/:environment_id" element={<EnvironmentDetailPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('EnvironmentDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    platformApi.getEnvironment.mockResolvedValue({
      id: 'moltbook',
      name: 'Moltbook',
      description: 'Canonical social-feed environment',
      runtime: 'openclaw',
      params_schema: {},
      populations: {
        resident: {
          path: 'populations/resident',
          default_count: 12,
          default_model: 'openai/gpt-5-mini',
        },
      },
    });
    platformApi.getRuntime.mockResolvedValue({
      id: 'openclaw',
      name: 'OpenClaw Runtime',
      description: 'Executes population folders with baseline tools.',
      required_population_files: ['AGENTS.md', 'HEARTBEAT.md', 'TOOLS.md'],
      baseline_tool_families: ['filesystem', 'shell'],
    });
    platformApi.validateEnvironment.mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
    });
    platformApi.listRuns.mockResolvedValue([
      {
        run_id: 'run-1',
        environment_id: 'moltbook',
        status: 'running',
        agent_count: 12,
      },
    ]);
    platformApi.createRun.mockResolvedValue({
      run_id: 'run-1',
    });
  });

  it('loads an environment and launches a run with resolved params and populations', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Launch Run');
    expect(platformApi.getEnvironment).toHaveBeenCalledWith('moltbook');
    expect(platformApi.getRuntime).toHaveBeenCalledWith('openclaw');
    expect(platformApi.validateEnvironment).toHaveBeenCalledWith('moltbook');
    expect(platformApi.listRuns).toHaveBeenCalledWith({ environment_id: 'moltbook' });
    expect(screen.getByText('run-1')).toBeInTheDocument();
    expect(screen.queryByText('Template Override')).not.toBeInTheDocument();
    expect(screen.getByText('Environment Authoring Model')).toBeInTheDocument();
    expect(screen.getByText('Minimum package')).toBeInTheDocument();
    expect(screen.getByText('Required backend API')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Start run' }));

    await waitFor(() => {
      expect(platformApi.createRun).toHaveBeenCalledTimes(1);
    });
    expect(platformApi.createRun).toHaveBeenCalledWith({
      environment_id: 'moltbook',
      params: {},
      population_overrides: {
        resident: {
          count: 12,
          model: 'openai/gpt-5-mini',
        },
      },
    });
    expect(mockToastSuccess).toHaveBeenCalledWith('Run run-1 started');
    expect(mockNavigate).toHaveBeenCalledWith('/runs/run-1/stats');
  });
});
