<script lang="ts">
  import { posts } from '$lib/utils/api';
  import { createEventDispatcher } from 'svelte';

  export let postId: string;
  export let score: number;
  export let userVote: 'up' | 'down' | null = null;
  export let vertical: boolean = true;

  const dispatch = createEventDispatcher();
  let isLoading = false;

  async function handleUpvote() {
    if (isLoading) return;
    isLoading = true;
    try {
      const result = await posts.upvote(postId);
      score = result.new_score;
      userVote = userVote === 'up' ? null : 'up';
      dispatch('vote', { type: 'up', score: result.new_score, upvotes: result.upvotes, downvotes: result.downvotes });
    } catch (e) {
      console.error('Failed to upvote:', e);
    } finally {
      isLoading = false;
    }
  }

  async function handleDownvote() {
    if (isLoading) return;
    isLoading = true;
    try {
      const result = await posts.downvote(postId);
      score = result.new_score;
      userVote = userVote === 'down' ? null : 'down';
      dispatch('vote', { type: 'down', score: result.new_score, upvotes: result.upvotes, downvotes: result.downvotes });
    } catch (e) {
      console.error('Failed to downvote:', e);
    } finally {
      isLoading = false;
    }
  }
</script>

<div class:flex-col={vertical} class:flex-row={!vertical} class="flex items-center space-y-1 {vertical ? '' : 'space-x-2'}">
  <button
    on:click={handleUpvote}
    disabled={isLoading}
    class="vote-btn {userVote === 'up' ? 'active' : ''}"
    aria-label="Upvote"
  >
    <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
      <path fill-rule="evenodd" d="M14.707 12.707a1 1 0 01-1.414 0L10 9.414l-3.293 3.293a1 1 0 01-1.414-1.414l4-4a1 1 0 011.414 0l4 4a1 1 0 010 1.414z" clip-rule="evenodd"/>
    </svg>
  </button>

  <span class="text-sm font-bold text-gray-300 min-w-[1.5rem] text-center">
    {score}
  </span>

  <button
    on:click={handleDownvote}
    disabled={isLoading}
    class="vote-btn {userVote === 'down' ? 'text-blue-500' : ''}"
    aria-label="Downvote"
  >
    <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
      <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
    </svg>
  </button>
</div>
