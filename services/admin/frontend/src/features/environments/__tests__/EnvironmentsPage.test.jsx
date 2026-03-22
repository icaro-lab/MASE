import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import EnvironmentsPage from '../EnvironmentsPage';
import { platformApi } from '@/api';

vi.mock('@/api', () => ({
  platformApi: {
    listEnvironments: vi.fn(),
  },
}));

function renderPage(route = '/environments') {
  return render(
    <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/environments" element={<EnvironmentsPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('EnvironmentsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    platformApi.listEnvironments.mockResolvedValue([
      {
        id: 'moltbook',
        name: 'Moltbook',
        runtime: 'openclaw',
        description: 'Canonical social-feed environment',
        world_base: 'moltbook',
        populations: {
          resident: {},
        },
        environment_skills: ['get_feed', 'create_post'],
      },
      {
        id: 'other-env',
        name: 'Other Environment',
        runtime: 'other-runtime',
        description: 'Other',
        populations: {},
        environment_skills: [],
      },
    ]);
  });

  it('filters environments by runtime query and local search', async () => {
    const user = userEvent.setup();
    renderPage('/environments?runtime=openclaw');

    await screen.findByText('Moltbook');
    expect(screen.queryByText('Other Environment')).not.toBeInTheDocument();
    expect(screen.getByText('Population roles')).toBeInTheDocument();
    expect(screen.getByText('Create An Experiment By Creating An Environment')).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText('Search environments'), 'molt');

    await waitFor(() => {
      expect(screen.getByText('Moltbook')).toBeInTheDocument();
    });
    expect(screen.getByText('runtime: openclaw')).toBeInTheDocument();
  });
});
