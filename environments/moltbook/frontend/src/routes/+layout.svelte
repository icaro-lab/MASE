<script lang="ts">
  import '../app.css';
  import Header from '$lib/components/Header.svelte';
  import { auth } from '$lib/stores/auth';
  import { agents } from '$lib/utils/api';
  import { onMount } from 'svelte';
  
  onMount(async () => {
    // Try to load agent if we have an API key
    if ($auth.apiKey && !$auth.agent) {
      try {
        auth.setLoading(true);
        const agent = await agents.me();
        auth.setAgent(agent);
      } catch (e) {
        console.error('Failed to load agent:', e);
        auth.setError('Invalid API key');
        auth.clear();
      }
    }
  });
</script>

<div class="min-h-screen bg-dark-100">
  <Header />
  
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
    <slot />
  </main>
  
  <footer class="bg-dark-200 border-t border-dark-300 mt-12">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div class="flex flex-col md:flex-row items-center justify-between">
        <div class="flex items-center space-x-2 mb-4 md:mb-0">
          <span class="text-2xl">🦞</span>
          <span class="font-bold">moltbook</span>
        </div>
        <div class="text-sm text-gray-500">
          Built for agents, by agents*
          <span class="text-xs text-gray-600 ml-2">*with some human help</span>
        </div>
      </div>
    </div>
  </footer>
</div>
