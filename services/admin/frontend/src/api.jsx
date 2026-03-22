import axios from 'axios';

function readApiBaseUrl() {
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  if (typeof process !== 'undefined' && process.env?.REACT_APP_API_URL) {
    return process.env.REACT_APP_API_URL;
  }
  return '/api';
}

// API base URL - uses /api/ prefix which is proxied to backend
const API_BASE_URL = readApiBaseUrl();

// Create axios instance
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30000,
});

// Error handler
const handleError = (error) => {
  const messageFromResponse =
    error?.response?.data?.detail ||
    error?.response?.data?.message ||
    error?.response?.statusText ||
    error?.message ||
    'Request failed';

  const wrappedError = new Error(messageFromResponse);

  if (error.response) {
    wrappedError.status = error.response.status;
    wrappedError.code = error.response.data?.code || null;
    wrappedError.data = error.response.data;
    throw wrappedError;
  } else if (error.request) {
    wrappedError.status = 0;
    wrappedError.code = 'network_error';
    wrappedError.message = 'No response from server. Please check your connection.';
    throw wrappedError;
  } else {
    wrappedError.status = null;
    wrappedError.code = 'request_setup_error';
    throw wrappedError;
  }
};

// Environment API
export const environmentApi = {
  // List all environments
  list: async () => {
    try {
      const response = await api.get('/environments');
      // Backend returns array directly, not wrapped in {environments: [...]}
      return Array.isArray(response.data) ? response.data : (response.data.environments || []);
    } catch (error) {
      handleError(error);
    }
  },

  // Start an environment
  start: async (name) => {
    try {
      const response = await api.post(`/environments/${name}/start`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  // Stop an environment
  stop: async (name) => {
    try {
      const response = await api.post(`/environments/${name}/stop`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },
};

export const platformApi = {
  listRuntimes: async () => {
    try {
      const response = await api.get('/v1/runtimes');
      return Array.isArray(response.data) ? response.data : [];
    } catch (error) {
      handleError(error);
    }
  },

  getRuntime: async (runtimeId) => {
    try {
      const response = await api.get(`/v1/runtimes/${encodeURIComponent(runtimeId)}`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  listEnvironments: async () => {
    try {
      const response = await api.get('/v1/environments');
      return Array.isArray(response.data) ? response.data : [];
    } catch (error) {
      handleError(error);
    }
  },

  getEnvironment: async (environmentId) => {
    try {
      const response = await api.get(`/v1/environments/${encodeURIComponent(environmentId)}`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  validateEnvironment: async (environmentId) => {
    try {
      const response = await api.post(`/v1/environments/${encodeURIComponent(environmentId)}/validate`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  createRun: async (runData) => {
    try {
      const response = await api.post('/v1/runs', runData);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  listRuns: async (params = {}) => {
    try {
      const response = await api.get('/v1/runs', { params });
      return Array.isArray(response.data) ? response.data : [];
    } catch (error) {
      handleError(error);
    }
  },

  getRun: async (runId) => {
    try {
      const response = await api.get(`/v1/runs/${encodeURIComponent(runId)}`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  getRunSnapshot: async (runId) => {
    try {
      const response = await api.get(`/v1/runs/${encodeURIComponent(runId)}/snapshot`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },
};

export const runApi = {
  list: async (params = {}) => {
    try {
      const response = await api.get('/v1/runs', { params });
      return Array.isArray(response.data) ? response.data : [];
    } catch (error) {
      handleError(error);
    }
  },

  get: async (runId) => {
    try {
      const response = await api.get(`/v1/runs/${runId}`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  getCost: async (runId) => {
    try {
      const response = await api.get(`/v1/runs/${runId}/cost`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  getSchedulerStatus: async (runId) => {
    try {
      const response = await api.get(`/v1/runs/${runId}/scheduler/status`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  getAgentContext: async (runId, params = {}) => {
    try {
      const response = await api.get(`/v1/runs/${runId}/agent-context`, { params });
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  stop: async (runId) => {
    try {
      const response = await api.post(`/v1/runs/${runId}/stop`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  pause: async (runId) => {
    try {
      const response = await api.post(`/v1/runs/${runId}/pause`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  resume: async (runId) => {
    try {
      const response = await api.post(`/v1/runs/${runId}/resume`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  restartStack: async (runId) => {
    try {
      const response = await api.post(`/v1/runs/${runId}/restart-stack`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  delete: async (runId) => {
    try {
      const response = await api.delete(`/v1/runs/${runId}`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },
};

// Events API
export const eventsApi = {
  getRunEvents: async (runId, eventType, agentId, limit = 100, offset = 0) => {
    try {
      const params = { limit, offset };
      if (eventType) params.event_type = eventType;
      if (agentId) params.agent_id = agentId;
      const response = await api.get(`/v1/events/runs/${runId}`, { params });
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  getRunEventCount: async (runId) => {
    try {
      const response = await api.get(`/v1/events/runs/${runId}/count`);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  exportEvents: async (requestData) => {
    try {
      const response = await api.post('/v1/events/export', requestData);
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },
};

// Health API
export const healthApi = {
  // Get overall system health
  get: async () => {
    try {
      const response = await api.get('/health');
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  // Liveness check
  liveness: async () => {
    try {
      const response = await api.get('/health/live');
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  // Readiness check
  readiness: async () => {
    try {
      const response = await api.get('/health/ready');
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },
};

export const platformSettingsApi = {
  getOpenRouterSettings: async () => {
    try {
      const response = await api.get('/settings/openrouter');
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },

  updateOpenRouterSettings: async (openrouterApiKey) => {
    try {
      const response = await api.put('/settings/openrouter', { openrouter_api_key: openrouterApiKey });
      return response.data;
    } catch (error) {
      handleError(error);
    }
  },
};

export default api;
