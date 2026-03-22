import asyncio

import pytest
from fastapi import HTTPException

from app.routes import launcher


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_agents_batch_preserves_input_order_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_agent_batch_result(
        request: launcher.CreateAgentRequest,
    ) -> launcher.CreateAgentBatchResult:
        if request.agent_id == "agent-2":
            await asyncio.sleep(0.01)
            return launcher.CreateAgentBatchResult(
                agent_id=request.agent_id,
                run_id=request.run_id or "default",
                created=False,
                error="boom",
            )
        return launcher.CreateAgentBatchResult(
            agent_id=request.agent_id,
            run_id=request.run_id or "default",
            created=True,
            path=f"/agents/{request.agent_id}",
        )

    monkeypatch.setattr(launcher, "_create_agent_batch_result", fake_create_agent_batch_result)

    response = await launcher.create_agents_batch(
        launcher.CreateAgentsBatchRequest(
            agents=[
                launcher.CreateAgentRequest(agent_id="agent-1", run_id="run-1"),
                launcher.CreateAgentRequest(agent_id="agent-2", run_id="run-1"),
                launcher.CreateAgentRequest(agent_id="agent-3", run_id="run-1"),
            ],
            concurrency=2,
        )
    )

    assert response.requested_count == 3
    assert response.created_count == 2
    assert response.error_count == 1
    assert [result.agent_id for result in response.results] == ["agent-1", "agent-2", "agent-3"]
    assert [result.created for result in response.results] == [True, False, True]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_agents_batch_rejects_duplicate_ids() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await launcher.create_agents_batch(
            launcher.CreateAgentsBatchRequest(
                agents=[
                    launcher.CreateAgentRequest(agent_id="agent-1", run_id="run-1"),
                    launcher.CreateAgentRequest(agent_id="agent-1", run_id="run-1"),
                ]
            )
        )

    assert exc_info.value.status_code == 400
    assert "duplicate agent ids" in str(exc_info.value.detail)
