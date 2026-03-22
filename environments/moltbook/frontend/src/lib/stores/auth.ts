import { writable } from 'svelte/store';
import { browser } from '$app/environment';

interface Agent {
  id: string;
  name: string;
  description?: string;
  karma: number;
  is_claimed: boolean;
  avatar_url?: string;
  follower_count: number;
  following_count: number;
}

interface AuthState {
  agent: Agent | null;
  apiKey: string | null;
  isLoading: boolean;
  error: string | null;
}

function createAuthStore() {
  const storedKey = browser ? localStorage.getItem('moltbook_api_key') : null;

  const { subscribe, set, update } = writable<AuthState>({
    agent: null,
    apiKey: storedKey,
    isLoading: false,
    error: null
  });

  return {
    subscribe,

    setApiKey: (key: string) => {
      if (browser) {
        localStorage.setItem('moltbook_api_key', key);
      }
      update(state => ({ ...state, apiKey: key, error: null }));
    },

    clear: () => {
      if (browser) {
        localStorage.removeItem('moltbook_api_key');
      }
      set({ agent: null, apiKey: null, isLoading: false, error: null });
    },

    setAgent: (agent: Agent) => {
      update(state => ({ ...state, agent, isLoading: false }));
    },

    setLoading: (loading: boolean) => {
      update(state => ({ ...state, isLoading: loading }));
    },

    setError: (error: string | null) => {
      update(state => ({ ...state, error, isLoading: false }));
    }
  };
}

export const auth = createAuthStore();
