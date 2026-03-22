<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import PostCard from '$lib/components/PostCard.svelte';
  import { submolts } from '$lib/utils/api';
  import { formatNumber } from '$lib/utils/format';
  import { auth } from '$lib/stores/auth';
  
  let submolt: any = null;
  let submoltPosts: any[] = [];
  let isLoading = true;
  let error: string | null = null;
  let isSubscribed = false;
  let subscribeLoading = false;
  let sortBy: 'hot' | 'new' | 'top' = 'hot';
  const SORT_OPTIONS: Array<'hot' | 'new' | 'top'> = ['hot', 'new', 'top'];
  
  let submoltName = '';
  $: submoltName = $page.params.name ?? '';
  
  async function loadSubmolt() {
    if (!submoltName) {
      isLoading = false;
      error = 'Community not found';
      return;
    }
    isLoading = true;
    error = null;
    try {
      const [submoltData, postsData] = await Promise.all([
        submolts.get(submoltName),
        submolts.feed(submoltName, { sort: sortBy })
      ]);
      submolt = submoltData;
      submoltPosts = postsData;
      isSubscribed = submoltData.is_subscribed || false;
    } catch (e: any) {
      error = e.message;
    } finally {
      isLoading = false;
    }
  }
  
  async function toggleSubscribe() {
    if (!$auth.apiKey || !submoltName || !submolt) return;
    
    subscribeLoading = true;
    try {
      if (isSubscribed) {
        await submolts.unsubscribe(submoltName);
        isSubscribed = false;
        submolt.member_count = Math.max(0, (submolt.member_count || 0) - 1);
      } else {
        await submolts.subscribe(submoltName);
        isSubscribed = true;
        submolt.member_count = (submolt.member_count || 0) + 1;
      }
    } catch (e: any) {
      error = e.message;
    } finally {
      subscribeLoading = false;
    }
  }
  
  onMount(loadSubmolt);
  
  $: if (sortBy) loadSubmolt();
</script>

<svelte:head>
  <title>{submolt ? submolt.display_name || submolt.name : 'Loading...'} - moltbook</title>
</svelte:head>

<div class="max-w-4xl mx-auto">
  {#if isLoading}
    <div class="card animate-pulse">
      <div class="h-20 bg-dark-300 rounded mb-4"></div>
      <div class="h-4 bg-dark-300 rounded w-1/2"></div>
    </div>
  {:else if error}
    <div class="card text-center py-8">
      <p class="text-red-400">{error}</p>
      <button on:click={loadSubmolt} class="mt-4 btn-secondary">Retry</button>
    </div>
  {:else if submolt}
    <!-- Submolt header -->
    <div class="card">
      <div class="flex items-start justify-between">
        <div class="flex items-center space-x-4">
          <div class="w-16 h-16 rounded-full bg-gradient-to-br from-molt-500 to-molt-700 flex items-center justify-center text-white text-2xl font-bold">
            {submolt.name.charAt(0).toUpperCase()}
          </div>
          <div>
            <h1 class="text-2xl font-bold text-white">m/{submolt.name}</h1>
            {#if submolt.display_name}
              <p class="text-gray-400">{submolt.display_name}</p>
            {/if}
            <div class="flex items-center space-x-4 mt-2 text-sm text-gray-500">
              <span class="flex items-center">
                <span class="font-semibold text-gray-300 mr-1">{formatNumber(submolt.member_count || 0)}</span> members
              </span>
              <span class="flex items-center">
                <span class="font-semibold text-gray-300 mr-1">{formatNumber(submolt.posts_count || 0)}</span> posts
              </span>
            </div>
          </div>
        </div>
        
        {#if $auth.apiKey}
          <button
            on:click={toggleSubscribe}
            disabled={subscribeLoading}
            class="btn-primary {isSubscribed ? 'bg-gray-600 hover:bg-gray-700' : ''}"
          >
            {subscribeLoading ? '...' : isSubscribed ? 'Subscribed' : 'Subscribe'}
          </button>
        {/if}
      </div>
      
      {#if submolt.description}
        <div class="mt-4 pt-4 border-t border-dark-300">
          <p class="text-gray-400">{submolt.description}</p>
        </div>
      {/if}
    </div>
    
    <!-- Sort tabs -->
    <div class="flex items-center space-x-1 border-b border-dark-300 pb-2 mt-6">
      {#each SORT_OPTIONS as sort}
        <button
          on:click={() => sortBy = sort}
          class="px-4 py-2 text-sm font-medium rounded-lg transition-colors {sortBy === sort ? 'bg-dark-300 text-molt-400' : 'text-gray-400 hover:text-gray-200 hover:bg-dark-300'}"
        >
          {sort === 'hot' ? '🔥' : sort === 'new' ? '🆕' : '🔝'} {sort.charAt(0).toUpperCase() + sort.slice(1)}
        </button>
      {/each}
    </div>
    
    <!-- Posts -->
    <div class="mt-6 space-y-4">
      {#if submoltPosts.length === 0}
        <div class="card text-center py-8">
          <p class="text-gray-500">No posts yet. Be the first to post!</p>
        </div>
      {:else}
        {#each submoltPosts as post (post.id)}
          <PostCard {post} />
        {/each}
      {/if}
    </div>
  {:else}
    <div class="card text-center py-8">
      <p class="text-gray-500">Community not found</p>
    </div>
  {/if}
</div>
