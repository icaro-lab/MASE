"""Minimal md-driven social runtime definitions for controlled experiments."""

from __future__ import annotations

from typing import Any, Dict, List


def credentials_filename_for_environment(environment_name: str) -> str:
    """Return the workspace credential file used for a specific environment."""
    normalized = str(environment_name or "").strip() or "environment"
    return f".{normalized}-credentials.json"

SIMPLIFIED_SOCIAL_LLM_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "register_on_environment",
            "description": "Register on the active environment and obtain an API token.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_feed",
            "description": "Fetch the latest environment feed.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                    "sort": {"type": "string", "enum": ["new", "top"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_post",
            "description": "Fetch a specific environment post by id.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"post_id": {"type": "string"}},
                "required": ["post_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_post_comments",
            "description": "Fetch comments for a specific environment post.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "post_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "sort": {"type": "string", "enum": ["new", "top", "controversial"]},
                },
                "required": ["post_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_comment",
            "description": "Write a comment on an environment post.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "post_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["post_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upvote_post",
            "description": "Upvote an environment post.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"post_id": {"type": "string"}},
                "required": ["post_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "downvote_post",
            "description": "Downvote an environment post.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"post_id": {"type": "string"}},
                "required": ["post_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heartbeat_ok",
            "description": "Finish the current heartbeat when nothing needs attention.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
    },
]

SIMPLIFIED_FEED_VOTE_LLM_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_feed",
            "description": "Fetch the latest environment feed cards.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    "sort": {"type": "string", "enum": ["new"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upvote_post",
            "description": "Upvote an environment post that is visible in the current feed.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"post_id": {"type": "string"}},
                "required": ["post_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "downvote_post",
            "description": "Downvote an environment post that is visible in the current feed.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"post_id": {"type": "string"}},
                "required": ["post_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heartbeat_ok",
            "description": "Finish the current heartbeat when there is nothing left to do.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
    },
]

SIMPLIFIED_SOCIAL_TOOL_SUMMARIES: Dict[str, str] = {
    "register_on_environment": "Register on the active environment when no valid token is available.",
    "get_feed": "Read the latest environment feed.",
    "get_post": "Read a concrete post by id.",
    "get_post_comments": "Read comments for a concrete post by id.",
    "create_comment": "Write a comment on a concrete post id you have actually seen.",
    "upvote_post": "Upvote a concrete post id you have actually seen.",
    "downvote_post": "Downvote a concrete post id you have actually seen.",
    "heartbeat_ok": "Stop the heartbeat when nothing needs attention.",
}

SIMPLIFIED_FEED_VOTE_TOOL_SUMMARIES: Dict[str, str] = {
    "get_feed": "Read the latest environment feed cards in recency order.",
    "upvote_post": "Upvote a concrete post id that is currently visible in the feed.",
    "downvote_post": "Downvote a concrete post id that is currently visible in the feed.",
    "heartbeat_ok": "Stop the heartbeat when nothing needs attention.",
}
