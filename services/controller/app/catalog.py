"""Discovery, validation, and materialization helpers for runtime/environment assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml


RUNTIME_MANIFEST = "runtime.yaml"
ENVIRONMENT_MANIFEST = "environment.yaml"
RUNTIME_ID_ALIASES = {
    "openclaw-py": "openclaw",
}


def _dedupe_paths(candidates: Iterable[str | Path]) -> list[Path]:
    results: list[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        path = Path(str(raw or "").strip())
        if not str(path):
            continue
        key = str(path.expanduser().resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        results.append(path)
    return results


def _walk_up_for_named_dir(start: Path, dir_name: str) -> list[Path]:
    results: list[Path] = []
    for base in [start, *start.parents]:
        candidate = base / dir_name
        if candidate.exists():
            results.append(candidate)
    return results


def candidate_runtime_roots() -> list[Path]:
    file_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()
    return _dedupe_paths(
        [
            "./runtimes",
            "/app/runtimes",
            *_walk_up_for_named_dir(cwd, "runtimes"),
            *_walk_up_for_named_dir(file_dir, "runtimes"),
        ]
    )


def candidate_environment_roots() -> list[Path]:
    file_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()
    return _dedupe_paths(
        [
            "./environments",
            "/app/environments",
            *_walk_up_for_named_dir(cwd, "environments"),
            *_walk_up_for_named_dir(file_dir, "environments"),
        ]
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping in {path}")
    return payload


def _read_text_if_exists(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _discover_manifests(roots: Iterable[Path], manifest_name: str) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name.startswith("_"):
                continue
            manifest_path = child / manifest_name
            if not manifest_path.is_file():
                continue
            payload = _load_yaml(manifest_path)
            payload.setdefault("id", child.name)
            payload["_manifest_path"] = str(manifest_path)
            manifests.append(payload)
    manifests.sort(key=lambda item: str(item.get("id") or ""))
    return manifests


def discover_runtimes(roots: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    return _discover_manifests(roots or candidate_runtime_roots(), RUNTIME_MANIFEST)


def discover_environments(roots: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    return _discover_manifests(roots or candidate_environment_roots(), ENVIRONMENT_MANIFEST)


def get_runtime(runtime_id: str, roots: Iterable[Path] | None = None) -> dict[str, Any] | None:
    normalized = str(runtime_id or "").strip()
    normalized = RUNTIME_ID_ALIASES.get(normalized, normalized)
    for manifest in discover_runtimes(roots):
        if str(manifest.get("id") or "").strip() == normalized:
            return manifest
    return None


def get_environment(environment_id: str, roots: Iterable[Path] | None = None) -> dict[str, Any] | None:
    normalized = str(environment_id or "").strip()
    for manifest in discover_environments(roots):
        if str(manifest.get("id") or "").strip() == normalized:
            return manifest
    return None


def get_environment_dir(
    environment_id: str,
    roots: Iterable[Path] | None = None,
) -> Path | None:
    manifest = get_environment(environment_id, roots)
    if manifest is None:
        return None
    manifest_path = Path(str(manifest["_manifest_path"]))
    return manifest_path.parent


def get_population_spec(
    environment_id: str,
    population_id: str,
    *,
    environment_roots: Iterable[Path] | None = None,
) -> tuple[dict[str, Any], Path]:
    manifest = get_environment(environment_id, environment_roots)
    if manifest is None:
        raise ValueError(f"environment not found: {environment_id}")
    populations = manifest.get("populations")
    if not isinstance(populations, dict):
        raise ValueError(f"environment {environment_id} has no populations mapping")
    spec = populations.get(population_id)
    if not isinstance(spec, dict):
        raise ValueError(f"population not found: {environment_id}/{population_id}")
    environment_dir = Path(str(manifest["_manifest_path"])).parent
    relative_path = str(spec.get("path") or f"populations/{population_id}").strip()
    return spec, environment_dir / relative_path


def load_population_materialization(
    environment_id: str,
    population_id: str,
    *,
    environment_roots: Iterable[Path] | None = None,
) -> dict[str, str]:
    spec, population_dir = get_population_spec(
        environment_id,
        population_id,
        environment_roots=environment_roots,
    )
    environment_dir = population_dir.parent.parent
    tools_manifest_path = environment_dir / str(spec.get("tools_manifest") or "").strip()
    tools_markdown = _read_text_if_exists(population_dir / "TOOLS.md")
    if tools_markdown is None and tools_manifest_path.is_file():
        try:
            tools_markdown = render_tools_markdown(environment_dir, tools_manifest_path)
        except Exception:
            tools_markdown = None

    materialization = {
        "agents": _read_text_if_exists(population_dir / "AGENTS.md") or "",
        "identity": _read_text_if_exists(population_dir / "IDENTITY.md") or "",
        "soul": _read_text_if_exists(population_dir / "SOUL.md") or "",
        "tools": tools_markdown or "",
        "bootstrap": _read_text_if_exists(population_dir / "BOOTSTRAP.md") or "",
        "user": _read_text_if_exists(population_dir / "USER.md") or "",
        "heartbeat": _read_text_if_exists(population_dir / "HEARTBEAT.md") or "",
    }
    return {
        key: value
        for key, value in materialization.items()
        if isinstance(value, str) and value.strip()
    }


def render_tools_markdown(environment_dir: Path, tools_manifest_path: Path) -> str:
    payload = _load_yaml(tools_manifest_path)
    runtime_tools = payload.get("runtime_tools")
    environment_skills = payload.get("environment_skills")
    local_tools = payload.get("local_tools")

    lines: list[str] = ["# TOOLS.md", ""]

    if isinstance(runtime_tools, list) and runtime_tools:
        lines.append("## Runtime Tools")
        for item in runtime_tools:
            tool_name = str(item or "").strip()
            if tool_name:
                lines.append(f"- `{tool_name}`")
        lines.append("")

    if isinstance(environment_skills, list) and environment_skills:
        lines.append("## Environment Skills")
        for raw_skill_id in environment_skills:
            skill_id = str(raw_skill_id or "").strip()
            if not skill_id:
                continue
            skill_manifest_path = environment_dir / "skills" / skill_id / "skill.yaml"
            skill_manifest = _load_yaml(skill_manifest_path) if skill_manifest_path.is_file() else {}
            description = str(skill_manifest.get("description") or "").strip()
            http_block = skill_manifest.get("http") if isinstance(skill_manifest.get("http"), dict) else {}
            method = str(http_block.get("method") or "").strip().upper()
            path = str(http_block.get("path") or "").strip()
            prompt_contract = (
                skill_manifest.get("prompt_contract")
                if isinstance(skill_manifest.get("prompt_contract"), dict)
                else {}
            )
            summary = str(prompt_contract.get("summary") or "").strip()

            lines.append(f"### {skill_id}")
            if description:
                lines.append(f"- {description}")
            if method or path:
                lines.append(f"- endpoint: `{method} {path}`".strip())
            if summary:
                lines.append(f"- usage: {summary}")
            lines.append("")

    if isinstance(local_tools, list) and local_tools:
        lines.append("## Local Tools")
        for item in local_tools:
            tool_name = str(item or "").strip()
            if tool_name:
                lines.append(f"- `{tool_name}`")
        lines.append("")

    if len(lines) == 2:
        lines.extend(
            [
                "No tools declared.",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _validate_environment_skill(environment_dir: Path, skill_id: str) -> list[str]:
    errors: list[str] = []
    skill_dir = environment_dir / "skills" / skill_id
    if not skill_dir.is_dir():
        errors.append(f"missing skill directory: skills/{skill_id}")
        return errors
    if not (skill_dir / "skill.yaml").is_file():
        errors.append(f"missing skill manifest: skills/{skill_id}/skill.yaml")
    if not (skill_dir / "SKILL.md").is_file():
        errors.append(f"missing skill prompt spec: skills/{skill_id}/SKILL.md")
    return errors


def _validate_tools_manifest(environment_dir: Path, relative_path: str) -> list[str]:
    errors: list[str] = []
    manifest_path = environment_dir / relative_path
    if not manifest_path.is_file():
        return [f"missing population tools manifest: {relative_path}"]
    try:
        payload = _load_yaml(manifest_path)
    except Exception as exc:
        return [f"invalid population tools manifest {relative_path}: {exc}"]

    environment_skills = payload.get("environment_skills")
    if isinstance(environment_skills, list):
        for raw_skill_id in environment_skills:
            skill_id = str(raw_skill_id or "").strip()
            if not skill_id:
                errors.append(f"invalid blank environment skill entry in {relative_path}")
                continue
            skill_dir = environment_dir / "skills" / skill_id
            if not skill_dir.is_dir():
                errors.append(
                    f"population tools manifest references unknown environment skill {skill_id}: {relative_path}"
                )
    return errors


def _validate_population(environment_dir: Path, population_id: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    relative_path = str(payload.get("path") or f"populations/{population_id}").strip()
    population_dir = environment_dir / relative_path
    if not population_dir.is_dir():
        errors.append(f"missing population directory: {relative_path}")
        return errors
    if not (population_dir / "AGENTS.md").is_file():
        errors.append(f"missing population instructions: {relative_path}/AGENTS.md")
    if not (population_dir / "HEARTBEAT.md").is_file():
        errors.append(f"missing population heartbeat: {relative_path}/HEARTBEAT.md")
    tools_manifest = str(payload.get("tools_manifest") or "").strip()
    if tools_manifest:
        errors.extend(_validate_tools_manifest(environment_dir, tools_manifest))
    elif not (population_dir / "TOOLS.md").is_file():
        errors.append(
            f"population {population_id} must provide TOOLS.md or declare tools_manifest"
        )
    return errors


def _validate_backend_contract(manifest: dict[str, Any], runtime_id: str, environment_dir: Path) -> list[str]:
    errors: list[str] = []
    backend_contract = manifest.get("backend_contract")
    if not isinstance(backend_contract, dict):
        return ["backend_contract must be a mapping"]

    required_endpoints = backend_contract.get("required_endpoints")
    if not isinstance(required_endpoints, list):
        return ["backend_contract.required_endpoints must be a list"]

    normalized_endpoints = {str(item or "").strip() for item in required_endpoints if str(item or "").strip()}
    for endpoint in ("GET /health", "GET /contract", "POST /auth/register"):
        if endpoint not in normalized_endpoints:
            errors.append(f"backend_contract.required_endpoints must include {endpoint}")

    if runtime_id == "openclaw":
        if not (environment_dir / "skill.md").is_file():
            errors.append("missing required runtime bootstrap document: skill.md")
        if "GET /skill.md" not in normalized_endpoints:
            errors.append("backend_contract.required_endpoints must include GET /skill.md")

    return errors


def _validate_run_hook(environment_dir: Path, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    hook_id = str(payload.get("id") or "").strip()
    if not hook_id:
        errors.append("run hook is missing id")
    script = str(payload.get("script") or "").strip()
    if not script:
        errors.append(f"run hook {hook_id or '<unknown>'} is missing script")
    elif not (environment_dir / script).is_file():
        errors.append(f"run hook script does not exist: {script}")
    trigger = str(payload.get("trigger") or "on_run_start").strip()
    if trigger not in {"on_run_start"}:
        errors.append(f"run hook {hook_id or '<unknown>'} has unsupported trigger: {trigger}")
    return errors


def _validate_launch_service(payload: dict[str, Any], field_name: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{field_name} must be a mapping"]
    for required_key in ("service_name", "container_prefix", "port"):
        value = str(payload.get(required_key) or "").strip()
        if not value:
            errors.append(f"{field_name}.{required_key} is required")
    compose_file = str(payload.get("compose_file") or "").strip()
    if field_name != "launch.frontend_service" and not compose_file:
        errors.append(f"{field_name}.compose_file is required")
    return errors


def _validate_launch_images(environment_id: str, payload: dict[str, Any], *, repo_root: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["launch.images must be a mapping"]

    environment_root = Path("environments") / environment_id

    def _is_within_environment(relative_path: str) -> bool:
        path = Path(relative_path)
        try:
            path.relative_to(environment_root)
            return True
        except ValueError:
            return False

    backend = payload.get("backend")
    if not isinstance(backend, dict):
        errors.append("launch.images.backend is required")
        return errors

    for asset_kind in ("backend", "frontend"):
        raw_asset = payload.get(asset_kind)
        if raw_asset is None:
            continue
        if not isinstance(raw_asset, dict):
            errors.append(f"launch.images.{asset_kind} must be a mapping")
            continue
        repository = str(raw_asset.get("repository") or "").strip()
        dockerfile = str(raw_asset.get("dockerfile") or "").strip()
        context = str(raw_asset.get("context") or "").strip()
        if not repository:
            errors.append(f"launch.images.{asset_kind}.repository is required")
        if not dockerfile:
            errors.append(f"launch.images.{asset_kind}.dockerfile is required")
        elif not _is_within_environment(dockerfile):
            errors.append(f"launch.images.{asset_kind}.dockerfile must stay inside environments/{environment_id}/")
        elif not (repo_root / dockerfile).is_file():
            errors.append(f"launch.images.{asset_kind}.dockerfile does not exist: {dockerfile}")
        if not context:
            errors.append(f"launch.images.{asset_kind}.context is required")
        elif not _is_within_environment(context):
            errors.append(f"launch.images.{asset_kind}.context must stay inside environments/{environment_id}/")
        elif not (repo_root / context).is_dir():
            errors.append(f"launch.images.{asset_kind}.context does not exist: {context}")
    return errors


def validate_environment(
    environment_id: str,
    *,
    environment_roots: Iterable[Path] | None = None,
    runtime_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    manifest = get_environment(environment_id, environment_roots)
    if manifest is None:
        return {
            "environment_id": environment_id,
            "valid": False,
            "errors": [f"environment not found: {environment_id}"],
            "warnings": [],
        }

    manifest_path = Path(str(manifest["_manifest_path"]))
    environment_dir = manifest_path.parent

    runtime_id = str(manifest.get("runtime") or "").strip()
    if not runtime_id:
        errors.append("missing runtime in environment manifest")
    elif get_runtime(runtime_id, runtime_roots) is None:
        errors.append(f"unknown runtime: {runtime_id}")

    for required_dir in ("backend", "skills", "populations"):
        if not (environment_dir / required_dir).is_dir():
            errors.append(f"missing required directory: {required_dir}/")

    skills = manifest.get("environment_skills")
    if not isinstance(skills, list) or not skills:
        errors.append("environment_skills must declare at least one skill")
    else:
        for skill_id in skills:
            errors.extend(_validate_environment_skill(environment_dir, str(skill_id)))

    populations = manifest.get("populations")
    if not isinstance(populations, dict) or not populations:
        errors.append("populations must declare at least one population")
    else:
        for population_id, payload in populations.items():
            if not isinstance(payload, dict):
                errors.append(f"population {population_id} must be a mapping")
                continue
            errors.extend(_validate_population(environment_dir, str(population_id), payload))

    if not isinstance(manifest.get("params_schema") or {}, dict):
        errors.append("params_schema must be a mapping")

    errors.extend(_validate_backend_contract(manifest, runtime_id, environment_dir))
    launch = manifest.get("launch") if isinstance(manifest.get("launch"), dict) else None
    if launch is None:
        errors.append("launch is required and must be a mapping")
    else:
        repo_root = environment_dir.parent.parent
        environment_service = launch.get("environment_service")
        if environment_service is None:
            errors.append("launch.environment_service is required")
        else:
            errors.extend(_validate_launch_service(environment_service, "launch.environment_service"))
            compose_file = str(environment_service.get("compose_file") or "").strip()
            if compose_file and not (repo_root / compose_file).is_file():
                errors.append(f"launch.environment_service.compose_file does not exist: {compose_file}")

        agent_worker_service = launch.get("agent_worker_service")
        if agent_worker_service is None:
            errors.append("launch.agent_worker_service is required")
        else:
            errors.extend(_validate_launch_service(agent_worker_service, "launch.agent_worker_service"))
            compose_file = str(agent_worker_service.get("compose_file") or "").strip()
            if compose_file and not (repo_root / compose_file).is_file():
                errors.append(f"launch.agent_worker_service.compose_file does not exist: {compose_file}")

        frontend_service = launch.get("frontend_service")
        if frontend_service is not None:
            errors.extend(_validate_launch_service(frontend_service, "launch.frontend_service"))
            compose_file = str(frontend_service.get("compose_file") or "").strip()
            if compose_file and not (repo_root / compose_file).is_file():
                errors.append(f"launch.frontend_service.compose_file does not exist: {compose_file}")

        launch_images = launch.get("images")
        errors.extend(_validate_launch_images(environment_id, launch_images, repo_root=repo_root))
        if frontend_service is not None:
            launch_images_payload = launch_images if isinstance(launch_images, dict) else {}
            if not isinstance(launch_images_payload.get("frontend"), dict):
                errors.append("launch.frontend_service requires launch.images.frontend")
    if not isinstance(manifest.get("runtime_defaults") or {}, dict):
        warnings.append("runtime_defaults is missing or not a mapping")
    if not isinstance(manifest.get("policy") or {}, dict):
        warnings.append("policy is missing or not a mapping")
    if not isinstance(manifest.get("data_sources") or {}, dict):
        warnings.append("data_sources is missing or not a mapping")

    run_hooks = manifest.get("run_hooks")
    if run_hooks is not None and not isinstance(run_hooks, list):
        errors.append("run_hooks must be a list when present")
    elif isinstance(run_hooks, list):
        for raw_hook in run_hooks:
            if not isinstance(raw_hook, dict):
                errors.append("run_hooks entries must be mappings")
                continue
            errors.extend(_validate_run_hook(environment_dir, raw_hook))

    return {
        "environment_id": environment_id,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
