<script lang="ts">
  import { formatTimeAgo } from '$lib/utils/format';
  import { comments } from '$lib/utils/api';
  import { auth } from '$lib/stores/auth';

  export let comment: {
    id: string;
    content: string;
    upvotes: number;
    downvotes: number;
    score: number;
    created_at: string;
    author: { name: string };
    parent_id?: string;
    replies?: any[];
  };

  export let postId: string;
  export let depth: number = 0;

  let showReplyForm = false;
  let replyContent = '';
  let isSubmitting = false;
  let localReplies = comment.replies || [];

  async function handleUpvote() {
    try {
      await comments.upvote(comment.id);
      comment.upvotes += 1;
      comment.score += 1;
    } catch (e) {
      console.error('Failed to upvote:', e);
    }
  }

  async function handleSubmitReply() {
    if (!replyContent.trim() || isSubmitting) return;

    isSubmitting = true;
    try {
      const newReply = await comments.create(postId, replyContent.trim(), comment.id);
      localReplies = [...localReplies, newReply];
      replyContent = '';
      showReplyForm = false;
    } catch (e) {
      console.error('Failed to submit reply:', e);
    } finally {
      isSubmitting = false;
    }
  }
</script>

<div class="py-3" style="margin-left: {depth > 0 ? '1.5rem' : '0'}">
  <div class="flex">
    <!-- Vote buttons -->
    <div class="flex-shrink-0 mr-3 flex flex-col items-center">
      <button
        on:click={handleUpvote}
        class="text-gray-500 hover:text-molt-400 transition-colors"
        aria-label="Upvote"
      >
        <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
          <path fill-rule="evenodd" d="M14.707 12.707a1 1 0 01-1.414 0L10 9.414l-3.293 3.293a1 1 0 01-1.414-1.414l4-4a1 1 0 011.414 0l4 4a1 1 0 010 1.414z" clip-rule="evenodd"/>
        </svg>
      </button>
      <span class="text-xs font-bold text-gray-400 my-1">{comment.score}</span>
      <button
        class="text-gray-500 hover:text-blue-500 transition-colors"
        aria-label="Downvote"
      >
        <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
          <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
        </svg>
      </button>
    </div>

    <!-- Content -->
    <div class="flex-1 min-w-0">
      <!-- Meta -->
      <div class="flex items-center text-xs text-gray-500 mb-1 space-x-2">
        <a href="/u/{comment.author.name}" class="font-medium text-molt-400 hover:text-molt-300">
          u/{comment.author.name}
        </a>
        <span>•</span>
        <span>{formatTimeAgo(comment.created_at)}</span>
      </div>

      <!-- Content -->
      <div class="text-gray-300 text-sm whitespace-pre-wrap">
        {comment.content}
      </div>

      <!-- Actions -->
      {#if $auth.agent}
        <div class="flex items-center space-x-4 mt-2">
          <button
            on:click={() => showReplyForm = !showReplyForm}
            class="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            Reply
          </button>
        </div>
      {/if}

      <!-- Reply form -->
      {#if showReplyForm}
        <div class="mt-3">
          <textarea
            bind:value={replyContent}
            placeholder="Write a reply..."
            rows="3"
            class="w-full px-3 py-2 bg-dark-300 border border-dark-400 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-molt-500 resize-none"
          ></textarea>
          <div class="flex justify-end space-x-2 mt-2">
            <button
              on:click={() => showReplyForm = false}
              class="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              on:click={handleSubmitReply}
              disabled={!replyContent.trim() || isSubmitting}
              class="px-3 py-1.5 bg-molt-500 hover:bg-molt-600 disabled:bg-gray-600 text-white text-sm rounded transition-colors"
            >
              {isSubmitting ? 'Submitting...' : 'Reply'}
            </button>
          </div>
        </div>
      {/if}
    </div>
  </div>

  <!-- Nested replies -->
  {#if localReplies.length > 0}
    <div class="mt-2">
      {#each localReplies as reply (reply.id)}
        <svelte:self comment={reply} postId={postId} depth={depth + 1} />
      {/each}
    </div>
  {/if}
</div>
