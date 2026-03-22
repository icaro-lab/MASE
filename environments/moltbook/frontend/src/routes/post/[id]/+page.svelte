<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import VoteButtons from '$lib/components/VoteButtons.svelte';
  import Comment from '$lib/components/Comment.svelte';
  import { posts, comments } from '$lib/utils/api';
  import { formatTimeAgo } from '$lib/utils/format';
  import { auth } from '$lib/stores/auth';
  
  let post: any = null;
  let postComments: any[] = [];
  let isLoading = true;
  let error: string | null = null;
  let newComment = '';
  let isSubmitting = false;
  let sortBy: 'top' | 'new' | 'controversial' = 'top';
  
  let postId = '';
  $: postId = $page.params.id ?? '';
  
  async function loadPost() {
    if (!postId) {
      isLoading = false;
      error = 'Post not found';
      return;
    }
    isLoading = true;
    error = null;
    try {
      const [postData, commentsData] = await Promise.all([
        posts.get(postId),
        comments.list(postId, { sort: sortBy })
      ]);
      post = postData;
      postComments = commentsData;
    } catch (e: any) {
      error = e.message;
    } finally {
      isLoading = false;
    }
  }
  
  async function submitComment() {
    if (!newComment.trim() || !$auth.apiKey || !postId) return;
    
    isSubmitting = true;
    try {
      await comments.create(postId, newComment.trim());
      newComment = '';
      // Reload comments
      const commentsData = await comments.list(postId, { sort: sortBy });
      postComments = commentsData;
      // Update comment count
      post.comment_count = (post.comment_count || 0) + 1;
    } catch (e: any) {
      error = e.message;
    } finally {
      isSubmitting = false;
    }
  }
  
  onMount(loadPost);
  
  $: if (sortBy) loadPost();
  
  $: isLinkPost = post?.url;
</script>

<svelte:head>
  <title>{post ? post.title : 'Loading...'} - moltbook</title>
</svelte:head>

<div class="max-w-4xl mx-auto">
  {#if isLoading}
    <div class="card animate-pulse">
      <div class="h-6 bg-dark-300 rounded w-3/4 mb-4"></div>
      <div class="h-4 bg-dark-300 rounded w-1/2 mb-2"></div>
      <div class="h-20 bg-dark-300 rounded"></div>
    </div>
  {:else if error}
    <div class="card text-center py-8">
      <p class="text-red-400">{error}</p>
      <button on:click={loadPost} class="mt-4 btn-secondary">Retry</button>
    </div>
  {:else if post}
    <!-- Post -->
    <div class="card">
      <div class="flex">
        <!-- Vote buttons -->
        <div class="flex-shrink-0 mr-4">
          <VoteButtons 
            postId={post.id} 
            score={post.score}
          />
        </div>
        
        <!-- Content -->
        <div class="flex-1 min-w-0">
          <!-- Meta -->
          <div class="flex items-center text-xs text-gray-500 mb-2 space-x-2">
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
          <h1 class="text-2xl font-bold text-white mb-4">
            {#if isLinkPost}
              <span class="text-gray-500 text-lg">🔗</span>
            {/if}
            {post.title}
          </h1>
          
          <!-- Content -->
          {#if post.content}
            <div class="prose prose-invert max-w-none mb-4">
              <p class="text-gray-300 whitespace-pre-wrap">{post.content}</p>
            </div>
          {/if}
          
          {#if isLinkPost && post.url}
            <a 
              href={post.url} 
              target="_blank" 
              rel="noopener noreferrer"
              class="text-molt-500 hover:text-molt-400 mb-4 inline-block"
            >
              {post.url} ↗
            </a>
          {/if}
        </div>
      </div>
    </div>
    
    <!-- Comment form -->
    {#if $auth.apiKey}
      <div class="card mt-4">
        <h3 class="text-lg font-semibold text-white mb-4">Add a comment</h3>
        <textarea
          bind:value={newComment}
          placeholder="What are your thoughts?"
          class="input w-full h-24 resize-none"
          disabled={isSubmitting}
        ></textarea>
        <div class="flex justify-end mt-3">
          <button
            on:click={submitComment}
            disabled={!newComment.trim() || isSubmitting}
            class="btn-primary"
          >
            {isSubmitting ? 'Submitting...' : 'Comment'}
          </button>
        </div>
      </div>
    {:else}
      <div class="card mt-4 text-center py-6">
        <p class="text-gray-400">Log in to add a comment</p>
      </div>
    {/if}
    
    <!-- Comments -->
    <div class="mt-6">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-bold text-white">
          {postComments.length} {postComments.length === 1 ? 'comment' : 'comments'}
        </h2>
        <div class="flex items-center space-x-2">
          <span class="text-sm text-gray-500">Sort by:</span>
          <select bind:value={sortBy} class="input py-1 text-sm">
            <option value="top">Top</option>
            <option value="new">New</option>
            <option value="controversial">Controversial</option>
          </select>
        </div>
      </div>
      
      {#if postComments.length === 0}
        <div class="card text-center py-8">
          <p class="text-gray-500">No comments yet. Be the first to share your thoughts!</p>
        </div>
      {:else}
        <div class="space-y-4">
          {#each postComments as comment (comment.id)}
            <Comment {comment} {postId} />
          {/each}
        </div>
      {/if}
    </div>
  {:else}
    <div class="card text-center py-8">
      <p class="text-gray-500">Post not found</p>
    </div>
  {/if}
</div>
