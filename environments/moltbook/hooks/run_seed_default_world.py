#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request


def _env_required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _write_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _request(
    environment_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    url = environment_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method.upper()} {path} failed: {exc.code} {detail}") from exc
    return json.loads(raw) if raw.strip() else {}


def _register(environment_url: str, payload: dict[str, Any]) -> str:
    response = _request(
        environment_url,
        "POST",
        "/auth/register",
        payload={
            "agent_id": payload["agent_id"],
            "name": payload["name"],
            "description": payload.get("description") or "",
        },
    )
    return str(response["api_token"])


def _create_post(environment_url: str, *, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request(
        environment_url,
        "POST",
        "/api/v1/posts",
        token=token,
        payload={
            "submolt": str(payload.get("submolt") or "general"),
            "title": str(payload["title"]),
            "content": str(payload["content"]),
        },
    )


def _create_comment(
    environment_url: str,
    *,
    token: str,
    post_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        environment_url,
        "POST",
        f"/api/v1/posts/{post_id}/comments",
        token=token,
        payload={"content": str(payload["content"])},
    )


def _vote(environment_url: str, *, token: str, post_id: str, kind: str) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"upvote", "downvote"}:
        raise RuntimeError(f"Unsupported vote kind: {kind}")
    return _request(
        environment_url,
        "POST",
        f"/api/v1/posts/{post_id}/{normalized_kind}",
        token=token,
    )


def _load_seed_payload(environment_dir: Path) -> dict[str, Any]:
    configured_path = str(os.getenv("MASE_MOLTBOOK_SEED_PATH") or "").strip()
    seed_path = Path(configured_path) if configured_path else environment_dir / "data" / "default_seed_world.json"
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("default seed payload must be a JSON object")
    return payload


def main() -> None:
    run_id = _env_required("MASE_RUN_ID")
    environment_url = _env_required("MASE_ENVIRONMENT_URL")
    environment_dir = Path(_env_required("MASE_ENVIRONMENT_DIR"))
    log_dir = Path(_env_required("MASE_RUN_LOG_DIR"))
    manifest_path = log_dir / "seed_manifest.jsonl"

    payload = _load_seed_payload(environment_dir)
    accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    posts = payload.get("posts") if isinstance(payload.get("posts"), list) else []
    comments = payload.get("comments") if isinstance(payload.get("comments"), list) else []
    votes = payload.get("votes") if isinstance(payload.get("votes"), list) else []

    _write_event(
        manifest_path,
        {
            "event": "seed_started",
            "run_id": run_id,
            "accounts": len(accounts),
            "posts": len(posts),
            "comments": len(comments),
            "votes": len(votes),
        },
    )

    tokens_by_name: dict[str, str] = {}
    for account in accounts:
        if not isinstance(account, dict):
            continue
        name = str(account.get("name") or "").strip()
        if not name:
            continue
        tokens_by_name[name] = _register(environment_url, account)
        _write_event(
            manifest_path,
            {
                "event": "seed_account_registered",
                "run_id": run_id,
                "agent_id": str(account.get("agent_id") or ""),
                "name": name,
            },
        )

    posts_by_title: dict[str, dict[str, Any]] = {}
    for item in posts:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        author = str(item.get("author") or "").strip()
        token = tokens_by_name.get(author)
        if not title or not token:
            continue
        created = _create_post(environment_url, token=token, payload=item)
        posts_by_title[title] = created
        _write_event(
            manifest_path,
            {
                "event": "seed_post_created",
                "run_id": run_id,
                "title": title,
                "post_id": created.get("id"),
                "author": author,
            },
        )

    for item in comments:
        if not isinstance(item, dict):
            continue
        author = str(item.get("author") or "").strip()
        post_title = str(item.get("post_title") or "").strip()
        token = tokens_by_name.get(author)
        target_post = posts_by_title.get(post_title) or {}
        post_id = str(target_post.get("id") or "").strip()
        if not token or not post_id:
            continue
        created = _create_comment(environment_url, token=token, post_id=post_id, payload=item)
        _write_event(
            manifest_path,
            {
                "event": "seed_comment_created",
                "run_id": run_id,
                "post_title": post_title,
                "post_id": post_id,
                "comment_id": created.get("id"),
                "author": author,
            },
        )

    for item in votes:
        if not isinstance(item, dict):
            continue
        voter = str(item.get("voter") or "").strip()
        post_title = str(item.get("post_title") or "").strip()
        token = tokens_by_name.get(voter)
        target_post = posts_by_title.get(post_title) or {}
        post_id = str(target_post.get("id") or "").strip()
        if not token or not post_id:
            continue
        result = _vote(environment_url, token=token, post_id=post_id, kind=str(item.get("kind") or ""))
        _write_event(
            manifest_path,
            {
                "event": "seed_vote_applied",
                "run_id": run_id,
                "post_title": post_title,
                "post_id": post_id,
                "voter": voter,
                "kind": str(item.get("kind") or ""),
                "applied": bool(result.get("applied", True)),
            },
        )

    _write_event(
        manifest_path,
        {
            "event": "seed_completed",
            "run_id": run_id,
            "created_posts": len(posts_by_title),
        },
    )


if __name__ == "__main__":
    main()
