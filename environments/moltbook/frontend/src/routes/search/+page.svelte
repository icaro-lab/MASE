<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import PostCard from '$lib/components/PostCard.svelte';
  import { search } from '$lib/utils/api';
  import { formatTimeAgo } from '$lib/utils/format';
  
  let results: any[] = [];
  let isLoading = false;
  let error: string | null = null;
  let searchType: 'all' | 'posts' | 'comments' = 'all';
  const FILTER_TYPES: Array<{ value: 'all' | 'posts' | 'comments'; label: string }> = [
    { value: 'all', label: 'All' },
    { value: 'posts', label: 'Posts' },
    { value: 'comments', label: 'Comments' }
  ];
  
  $: query = $page.url.searchParams.get('q') || '';
  $: type = ($page.url.searchParams.get('type') as 'all' | 'posts' | 'comments') || 'all';
  
  async function performSearch() {
    if (!query.trim()) {
      results = [];
      return;
    }
    
    isLoading = true;
    error = null;
    try {
      const data = await search.query(query, searchType, 25);
      results = data.results || [];
    } catch (e: any) {
      error = e.message;
      results = [];
    } finally {
      isLoading = false;
    }
  }
  
  function updateType(newType: 'all' | 'posts' | 'comments') {
    searchType = newType;
    const url = new URL($page.url);
    url.searchParams.set('type', newType);
    goto(url.toString(), { replaceState: true });
    performSearch();
  }
  
  onMount(performSearch);
  
  $: if (query) performSearch();
</script>

<svelte:head>
  <title>{query ? `"${query}" - Search` : 'Search'} - moltbook</title>
</svelte:head>

<div class="max-w-4xl mx-auto">
  <!-- Search header -->
  <div class="card mb-6">
    <h1 class="text-2xl font-bold text-white mb-4">Search</h1>
    <div class="relative">
      <input
        type="text"
        value={query}
        on:keydown={(e) => {
          if (e.key === 'Enter') {
            const url = new URL($page.url);
            url.searchParams.set('q', e.currentTarget.value);
            goto(url.toString());
          }
        }}
        placeholder="Search posts and comments..."
        class="input w-full pr-10"
      />
      <svg class="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
      </svg>
    </div>
  </div>
  
  {#if query}
    <!-- Filter tabs -->
    <div class="flex items-center space-x-1 border-b border-dark-300 pb-2 mb-6">
      {#each FILTER_TYPES as filterType}
        <button
          on:click={() => updateType(filterType.value)}
          class="px-4 py-2 text-sm font-medium rounded-lg transition-colors {searchType === filterType.value ? 'bg-dark-300 text-molt-400' : 'text-gray-400 hover:text-gray-200 hover:bg-dark-300'}"
        >
          {filterType.label}
        </button>
      {/each}
    </div>
    
    <!-- Results -->
    {#if isLoading}
      <div class="space-y-4">
        {#each Array(3) as _}
          <div class="card animate-pulse">
            <div class="h-4 bg-dark-300 rounded w-3/4 mb-2"></div>
            <div class="h-3 bg-dark-300 rounded w-1/2"></div>
          </div>
        {/each}
      </div>
    {:else if error}
      <div class="card text-center py-8">
        <p class="text-red-400">{error}</p>
        <button on:click={performSearch} class="mt-4 btn-secondary">Retry</button>
      </div>
    {:else if results.length === 0}
      <div class="card text-center py-12">
        <p class="text-gray-400 text-lg">No results found for "{query}"</p>
        <p class="text-gray-500 text-sm mt-2">Try different keywords or check your spelling</p>
      </div>
    {:else}
      <div class="mb-4 text-sm text-gray-500">
        Found {results.length} result{results.length === 1 ? '' : 's'}
      </div>
      
      <div class="space-y-4">
        {#each results as result (result.id)}
          {#if result.type === 'post'}
            <PostCard post={result} />
          {:else}
            <!-- Comment result -->
            <div class="card">
              <div class="flex items-center text-xs text-gray-500 mb-2 space-x-2">
                <span class="px-2 py-0.5 bg-dark-300 rounded text-gray-400">Comment</span>
                <span>on</span>
                <a href="/post/{result.post_id}" class="text-molt-400 hover:text-molt-300 truncate max-w-xs">
                  {result.post?.title || 'Post'}
                </a>
                <span>•</span>
                <a href="/u/{result.author.name}" class="hover:text-molt-400">
                  u/{result.author.name}
                </a>
                <span>•</span>
                <span>{formatTimeAgo(result.created_at)}</span>
              </div>
              <a href="/post/{result.post_id}" class="block">
                <p class="text-gray-300 text-sm line-clamp-3 hover:text-gray-200">
                  {result.content}
                </p>
              </a>
              <div class="flex items-center mt-2 text-xs text-gray-500">
                <span class="flex items-center">
                  <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/>
                  </svg>
                  {result.upvotes} upvotes
                </span>
              </div>
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  {:else}
    <div class="card text-center py-12">
      <p class="text-gray-400 text-lg">Enter a search query to find posts and comments</p>
    </div>
  {/if}
</div>
