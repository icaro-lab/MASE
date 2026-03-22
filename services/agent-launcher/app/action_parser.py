"""Action parser for LLM responses."""

import re
import json
from typing import List, Dict, Any, Optional, Union
from enum import Enum
from pydantic import BaseModel


class ActionType(str, Enum):
    """Types of actions the agent can perform."""
    HTTP = "http"
    FILESYSTEM = "fs"
    HEARTBEAT_OK = "heartbeat_ok"
    UNKNOWN = "unknown"


class HTTPMethod(str, Enum):
    """HTTP methods supported for http actions."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class Action(BaseModel):
    """Represents an action to be executed."""
    action: ActionType
    action_name: Optional[str] = None
    action_key: Optional[str] = None
    # HTTP action fields
    method: Optional[HTTPMethod] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    body: Optional[Any] = None
    # Filesystem action fields
    operation: Optional[str] = None  # write, append, delete
    path: Optional[str] = None
    content: Optional[str] = None
    find: Optional[str] = None
    replace: Optional[str] = None
    replace_all: Optional[bool] = None
    # Additional metadata
    description: Optional[str] = None
    requires_confirmation: bool = False


class ParsedResponse(BaseModel):
    """Parsed LLM response containing actions."""
    raw_response: str
    actions: List[Action]
    text_content: str  # Non-action text content


class ActionParser:
    """Parser for extracting actions from LLM responses."""
    FUNCTION_TOOL_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    
    # Regex patterns for extracting JSON from markdown code blocks
    JSON_BLOCK_PATTERN = re.compile(
        r'```(?:json)?\s*\n(.*?)\n```',
        re.DOTALL | re.IGNORECASE
    )
    
    # Pattern for inline JSON objects
    INLINE_JSON_PATTERN = re.compile(
        r'\{[^{}]*"action"[^{}]*\}',
        re.DOTALL
    )
    
    def parse(self, response: str) -> ParsedResponse:
        """Parse LLM response and extract actions.
        
        Tries multiple parsing strategies:
        1. Extract JSON from markdown code blocks
        2. Find inline JSON objects
        3. Look for structured action patterns
        """
        actions: List[Action] = []
        remaining_text = response
        
        # Strategy 1: Extract from markdown code blocks
        code_blocks = self.JSON_BLOCK_PATTERN.findall(response)
        for block in code_blocks:
            try:
                action = self._parse_action_json(block.strip())
                if action:
                    actions.append(action)
                    # Remove this block from remaining text
                    remaining_text = remaining_text.replace(
                        f"```json\n{block}\n```", ""
                    ).replace(f"```\n{block}\n```", "")
            except json.JSONDecodeError:
                continue
        
        # Strategy 2: Find inline JSON objects
        inline_matches = self.INLINE_JSON_PATTERN.findall(response)
        for match in inline_matches:
            try:
                action = self._parse_action_json(match.strip())
                if action and action not in actions:
                    actions.append(action)
                    remaining_text = remaining_text.replace(match, "")
            except json.JSONDecodeError:
                continue
        
        # Strategy 3: Check if response is pure JSON
        if not actions:
            try:
                action = self._parse_action_json(response.strip())
                if action:
                    actions.append(action)
                    remaining_text = ""
            except json.JSONDecodeError:
                pass
        
        # Clean up remaining text
        text_content = self._clean_text(remaining_text)
        
        return ParsedResponse(
            raw_response=response,
            actions=actions,
            text_content=text_content
        )
    
    def _parse_action_json(self, json_str: str) -> Optional[Action]:
        """Parse a JSON string into an Action."""
        data = json.loads(json_str)
        
        # Handle both single action and action array
        if isinstance(data, list):
            # Return only the first action for now
            # The caller should handle multiple actions differently
            if not data:
                return None
            data = data[0]
        
        if not isinstance(data, dict):
            return None
        
        action_type = data.get("action", "").lower()
        
        if action_type == "heartbeat_ok":
            return Action(action=ActionType.HEARTBEAT_OK)
        
        elif action_type == "http":
            return Action(
                action=ActionType.HTTP,
                action_name=data.get("action_name"),
                action_key=data.get("action_key"),
                method=HTTPMethod(data.get("method", "GET").upper()),
                url=data.get("url"),
                headers=data.get("headers", {}),
                body=data.get("body"),
                description=data.get("description"),
                requires_confirmation=data.get("requires_confirmation", False),
            )
        
        elif action_type == "fs":
            return Action(
                action=ActionType.FILESYSTEM,
                action_name=data.get("action_name"),
                action_key=data.get("action_key"),
                operation=data.get("operation", "write"),
                path=data.get("path"),
                content=data.get("content"),
                find=data.get("find"),
                replace=data.get("replace"),
                replace_all=data.get("replace_all"),
                description=data.get("description"),
                requires_confirmation=data.get("requires_confirmation", False),
            )
        
        else:
            # Unknown action type
            return Action(
                action=ActionType.UNKNOWN,
                description=f"Unknown action type: {action_type}"
            )
    
    def _clean_text(self, text: str) -> str:
        """Clean up extracted text content."""
        # Remove excessive whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove trailing/leading whitespace
        text = text.strip()
        return text
    
    def parse_multiple(self, response: str) -> List[Action]:
        """Parse multiple actions from a response.
        
        Handles:
        - HEARTBEAT_OK
        - Direct tool calls like request({...})
        - JSON array of actions
        - Multiple code blocks
        - Action delimiters
        """
        actions: List[Action] = []
        normalized = str(response or "").strip()

        if normalized == "HEARTBEAT_OK":
            return [Action(action=ActionType.HEARTBEAT_OK)]

        direct_actions = self._parse_direct_tool_calls(normalized)
        if direct_actions:
            return direct_actions
        
        # Try parsing as JSON array first
        try:
            data = json.loads(normalized)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        action = self._dict_to_action(item)
                        if action:
                            actions.append(action)
                return actions
        except json.JSONDecodeError:
            pass
        
        # Fall back to standard parsing
        parsed = self.parse(response)
        return parsed.actions

    def _parse_direct_tool_calls(self, response: str) -> List[Action]:
        raw = str(response or "")
        if not raw:
            return []

        decoder = json.JSONDecoder()
        cursor = 0
        actions: List[Action] = []

        while cursor < len(raw):
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor >= len(raw):
                break

            match = self.FUNCTION_TOOL_PATTERN.match(raw, cursor)
            if not match:
                return []

            tool_name = str(match.group(1) or "").strip().lower()
            args_start = match.end()
            try:
                args_value, consumed = decoder.raw_decode(raw[args_start:])
            except json.JSONDecodeError:
                return []

            next_index = args_start + consumed
            while next_index < len(raw) and raw[next_index].isspace():
                next_index += 1
            if next_index >= len(raw) or raw[next_index] != ")":
                return []

            if not isinstance(args_value, dict):
                return []

            action = self._tool_call_to_action(tool_name, args_value)
            if action is None:
                return []
            actions.append(action)
            cursor = next_index + 1

        return actions

    def _tool_call_to_action(self, tool_name: str, args: Dict[str, Any]) -> Optional[Action]:
        if tool_name == "read":
            return Action(
                action=ActionType.FILESYSTEM,
                operation="read",
                path=str(args.get("path") or ""),
            )

        if tool_name == "write":
            return Action(
                action=ActionType.FILESYSTEM,
                operation="write",
                path=str(args.get("path") or ""),
                content=None if args.get("content") is None else str(args.get("content")),
            )

        if tool_name == "append":
            return Action(
                action=ActionType.FILESYSTEM,
                operation="append",
                path=str(args.get("path") or ""),
                content=None if args.get("content") is None else str(args.get("content")),
            )

        if tool_name == "edit":
            return Action(
                action=ActionType.FILESYSTEM,
                operation="edit",
                path=str(args.get("path") or ""),
                find=None if args.get("find") is None else str(args.get("find")),
                replace=None if args.get("replace") is None else str(args.get("replace")),
                replace_all=bool(args.get("replace_all")) if args.get("replace_all") is not None else False,
            )

        if tool_name == "delete":
            return Action(
                action=ActionType.FILESYSTEM,
                operation="delete",
                path=str(args.get("path") or ""),
            )

        if tool_name == "request":
            method_raw = str(args.get("method") or "GET").strip().upper()
            try:
                method = HTTPMethod(method_raw)
            except ValueError:
                return None
            url = args.get("url") or args.get("path")
            body = args.get("body")
            if body is None:
                body = args.get("json")
            return Action(
                action=ActionType.HTTP,
                method=method,
                url=None if url is None else str(url),
                headers=args.get("headers", {}) if isinstance(args.get("headers"), dict) else {},
                body=body,
            )

        if tool_name == "heartbeat_ok":
            return Action(action=ActionType.HEARTBEAT_OK)

        if tool_name == "register_on_environment":
            body = {
                "agent_id": "__SELF_AGENT_ID__",
                "name": str(args.get("name") or "").strip(),
                "description": str(args.get("description") or "").strip(),
            }
            return Action(
                action=ActionType.HTTP,
                action_name="register_on_environment",
                method=HTTPMethod.POST,
                url="/auth/register",
                headers={},
                body=body,
            )

        if tool_name == "get_feed":
            limit = int(args.get("limit") or 15)
            sort = str(args.get("sort") or "new").strip() or "new"
            return Action(
                action=ActionType.HTTP,
                action_name="get_feed",
                method=HTTPMethod.GET,
                url=f"/api/v1/feed?sort={sort}&limit={limit}",
                headers={},
            )

        if tool_name == "get_post":
            post_id = str(args.get("post_id") or "").strip()
            return Action(
                action=ActionType.HTTP,
                action_name="get_post",
                method=HTTPMethod.GET,
                url=f"/api/v1/posts/{post_id}",
                headers={},
            )

        if tool_name == "get_post_comments":
            post_id = str(args.get("post_id") or "").strip()
            limit = int(args.get("limit") or 35)
            sort = str(args.get("sort") or "new").strip() or "new"
            return Action(
                action=ActionType.HTTP,
                action_name="get_post_comments",
                method=HTTPMethod.GET,
                url=f"/api/v1/posts/{post_id}/comments?sort={sort}&limit={limit}",
                headers={},
            )

        if tool_name == "upvote_post":
            post_id = str(args.get("post_id") or "").strip()
            return Action(
                action=ActionType.HTTP,
                action_name="upvote_post",
                method=HTTPMethod.POST,
                url=f"/api/v1/posts/{post_id}/upvote",
                headers={},
                body={},
            )

        if tool_name == "downvote_post":
            post_id = str(args.get("post_id") or "").strip()
            return Action(
                action=ActionType.HTTP,
                action_name="downvote_post",
                method=HTTPMethod.POST,
                url=f"/api/v1/posts/{post_id}/downvote",
                headers={},
                body={},
            )

        if tool_name == "create_comment":
            payload = {
                "content": str(args.get("content") or "").strip(),
            }
            parent_id = str(args.get("parent_id") or "").strip()
            if parent_id:
                payload["parent_id"] = parent_id
            post_id = str(args.get("post_id") or "").strip()
            return Action(
                action=ActionType.HTTP,
                action_name="create_comment",
                method=HTTPMethod.POST,
                url=f"/api/v1/posts/{post_id}/comments",
                headers={},
                body=payload,
            )
        return None
    
    def _dict_to_action(self, data: Dict[str, Any]) -> Optional[Action]:
        """Convert a dictionary to an Action."""
        action_type = data.get("action", "").lower()
        
        try:
            if action_type == "heartbeat_ok":
                return Action(action=ActionType.HEARTBEAT_OK)
            
            elif action_type == "http":
                return Action(
                    action=ActionType.HTTP,
                    action_name=data.get("action_name"),
                    action_key=data.get("action_key"),
                    method=HTTPMethod(data.get("method", "GET").upper()),
                    url=data.get("url"),
                    headers=data.get("headers", {}),
                    body=data.get("body"),
                    description=data.get("description"),
                    requires_confirmation=data.get("requires_confirmation", False),
                )
            
            elif action_type == "fs":
                return Action(
                    action=ActionType.FILESYSTEM,
                    action_name=data.get("action_name"),
                    action_key=data.get("action_key"),
                    operation=data.get("operation", "write"),
                    path=data.get("path"),
                    content=data.get("content"),
                    description=data.get("description"),
                    requires_confirmation=data.get("requires_confirmation", False),
                )
        except (ValueError, KeyError):
            pass
        
        return None
    
    def extract_confirmation_code(self, response: str) -> Optional[str]:
        """Extract confirmation code from a pending confirmation response."""
        try:
            data = json.loads(response.strip())
            if data.get("status") == "pending_confirmation":
                return data.get("confirmation_code")
        except json.JSONDecodeError:
            # Try regex extraction
            pattern = r'"confirmation_code"\s*:\s*"([^"]+)"'
            match = re.search(pattern, response)
            if match:
                return match.group(1)
        
        return None


# Global parser instance
action_parser = ActionParser()


def parse_actions(response: str) -> ParsedResponse:
    """Parse actions from an LLM response."""
    return action_parser.parse(response)


def parse_multiple_actions(response: str) -> List[Action]:
    """Parse multiple actions from an LLM response."""
    return action_parser.parse_multiple(response)
