import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';

import RuntimesPage from '../RuntimesPage';
import { platformApi } from '@/api';

vi.mock('@/api', () => ({
  platformApi: {
    listRuntimes: vi.fn(),
  },
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/runtimes']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/runtimes" element={<RuntimesPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('RuntimesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    platformApi.listRuntimes.mockResolvedValue([
      {
        id: 'openclaw',
        name: 'OpenClaw Runtime',
        required_population_files: ['AGENTS.md', 'HEARTBEAT.md', 'TOOLS.md'],
        baseline_tool_families: ['filesystem', 'shell'],
      },
    ]);
  });

  it('renders runtime metadata and environment filter link', async () => {
    renderPage();

    await screen.findByText('OpenClaw Runtime');
    expect(screen.queryByText('simple-feed-voter')).not.toBeInTheDocument();
    expect(screen.queryByText('openclaw_py_core')).not.toBeInTheDocument();
    expect(screen.getByText('filesystem')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /view environments using this runtime/i })).toHaveAttribute(
      'href',
      '/environments?runtime=openclaw'
    );
  });
});
