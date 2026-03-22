from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pathlib import Path

router = APIRouter()

DOC_ROOT = Path("/app")


def _read_doc(name: str) -> str:
    path = DOC_ROOT / name
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return f"# Missing {name}\n\nThis environment build did not include `{name}`.\n"


@router.get("/skill.md", response_class=PlainTextResponse)
def skill_md() -> str:
    return _read_doc("skill.md")


@router.get("/heartbeat.md", response_class=PlainTextResponse)
def heartbeat_md() -> str:
    return _read_doc("heartbeat.md")


@router.get("/messaging.md", response_class=PlainTextResponse)
def messaging_md() -> str:
    return _read_doc("messaging.md")


@router.get("/rules.md", response_class=PlainTextResponse)
def rules_md() -> str:
    return _read_doc("rules.md")
