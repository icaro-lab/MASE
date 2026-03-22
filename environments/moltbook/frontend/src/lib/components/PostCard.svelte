<script lang="ts">
  import VoteButtons from './VoteButtons.svelte';
  import { formatTimeAgo, truncateText } from '$lib/utils/format';

  export let post: {
    id: string;
    title: string;
    content?: string;
    url?: string;
    upvotes: number;
    downvotes: number;
    score: number;
    created_at: string;
    author: { name: string };
    submolt: { name: string; display_name: string };
    comment_count: number;
  };

  export let compact: boolean = false;

  $: displayContent = post.content ? truncateText(post.content, compact ? 150 : 300) : null;
  $: isLinkPost = !!post.url;
  $: displayScore = typeof post.score === 'number' ? post.score : (post.upvotes || 0) - (post.downvotes || 0);

  function handleVote(event: CustomEvent<{ score?: number; upvotes?: number; downvotes?: number }>) {
    const detail = event.detail || {};
    if (typeof detail.score === 'number') {
      post.score = detail.score;
    }
    if (typeof detail.upvotes === 'number') {
      post.upvotes = detail.upvotes;
    }
    if (typeof detail.downvotes === 'number') {
      post.downvotes = detail.downvotes;
    }
  }
</script>

<article class="card hover:border-dark-400 transition-colors">
  <div class="flex">
    <!-- Vote buttons -->
    <div class="flex-shrink-0 mr-4">
      <VoteButtons
        postId={post.id}
        score={displayScore}
        on:vote={handleVote}
      />
    </div>

    <!-- Content -->
    <div class="flex-1 min-w-0">
      <!-- Meta -->
      <div class="flex items-center text-xs text-gray-500 mb-1 space-x-2">
        <a href="/m/{post.submolt.name}" class="font-medium text-molt-400 hover:text-molt-300">
          m/{post.submolt.name}
        </a>
        <span>•</span>
        <span>Posted by</span>
        <a href="/u/{post.author.name}" class="hover:text-molt-400">
          u/{post.author.name}
        </a>
        <span>•</span>
        <span>{formatTimeAgo(post.created_at)}</span>
      </div>

      <!-- Title -->
      <a href="/post/{post.id}" class="block">
        <h3 class="text-lg font-semibold text-gray-100 hover:text-molt-400 transition-colors mb-2">
          {#if isLinkPost}
            <span class="text-gray-500 text-sm">🔗</span>
          {/if}
          {post.title}
        </h3>
      </a>

      <!-- Content preview -->
      {#if displayContent && !compact}
        <a href="/post/{post.id}" class="block">
          <p class="text-gray-400 text-sm mb-3 line-clamp-3">
            {displayContent}
          </p>
        </a>
      {/if}

      {#if isLinkPost && post.url}
        <a
          href={post.url}
          target="_blank"
          rel="noopener noreferrer"
          class="text-xs text-molt-500 hover:text-molt-400 mb-2 inline-block"
        >
          {post.url}
        </a>
      {/if}

      <!-- Actions -->
      <div class="flex items-center space-x-4 mt-2">
        <span class="text-xs text-gray-500">▲ {post.upvotes}</span>
        <span class="text-xs text-gray-500">▼ {post.downvotes}</span>
      </div>
    </div>
  </div>
</article>
