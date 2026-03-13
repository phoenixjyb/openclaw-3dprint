"""End-to-end pipeline test — validates the full orchestration flow with mocked services.

This test simulates: request → LLM interpret → mesh generate → (optional transfer) → slice → print
with all external calls mocked, verifying the state machine transitions and approval flow.

Run: python -m pytest tests/test_e2e_pipeline.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.models.job import JobStage, PrintJob
from pipeline.orchestrator import Orchestrator
from pipeline.utils.config import Settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Create a Settings object with test values."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MESH_PROVIDER", "tripo")
    monkeypatch.setenv("TRIPO_API_KEY", "tsk-test")
    monkeypatch.setenv("MESHY_API_KEY", "")
    monkeypatch.setenv("SLICER_MODE", "local")
    monkeypatch.setenv("SLICER_PATH", "/usr/local/bin/prusa-slicer")
    monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("BAMBU_PRINTER_IP", "")
    monkeypatch.setenv("BAMBU_PRINTER_SERIAL", "")
    monkeypatch.setenv("BAMBU_PRINTER_ACCESS_CODE", "")
    monkeypatch.setenv("BAMBU_SEND_METHOD", "ftp")
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def orchestrator(settings):
    """Create an Orchestrator with mocked callbacks."""
    send_message = AsyncMock()
    send_photo = AsyncMock()
    request_approval = AsyncMock()

    orch = Orchestrator(
        settings=settings,
        send_message=send_message,
        send_photo=send_photo,
        request_approval=request_approval,
    )
    return orch


def _auto_approve(orch: Orchestrator):
    """Patch request_approval to auto-approve and resolve the future."""
    original = orch.request_approval

    async def _approve_and_resolve(chat_id, job_id, text):
        await original(chat_id, job_id, text)
        await orch.resolve_approval(job_id, True)

    orch.request_approval = AsyncMock(side_effect=_approve_and_resolve)


@pytest.mark.asyncio
async def test_full_pipeline_happy_path(orchestrator, settings, tmp_path):
    """Test the complete pipeline with all services mocked and auto-approval."""
    orch = orchestrator
    _auto_approve(orch)

    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)

    # Mock LLM interpretation
    mock_interpret_result = MagicMock()
    mock_interpret_result.enriched_prompt = "A detailed Cinderella figurine, standing pose"
    mock_interpret_result.object_name = "Cinderella figurine"
    mock_interpret_result.suggested_scale_mm = 120.0
    mock_interpret_result.suggested_material = "PLA"
    mock_interpret_result.art_style = "Disney-inspired cartoon"
    mock_interpret_result.orientation_notes = "Print upright"

    # Mock mesh generation
    dummy_stl = staging / "test_job" / "abc123.stl"
    dummy_stl.parent.mkdir(parents=True, exist_ok=True)
    dummy_stl.write_bytes(b"fake STL data")
    dummy_thumb = staging / "test_job" / "abc123_thumb.png"
    dummy_thumb.write_bytes(b"fake PNG")

    mock_mesh_result = MagicMock()
    mock_mesh_result.task_id = "abc123"
    mock_mesh_result.model_url = "https://example.com/model.stl"
    mock_mesh_result.thumbnail_url = "https://example.com/thumb.png"
    mock_mesh_result.model_local_path = str(dummy_stl)
    mock_mesh_result.thumbnail_local_path = str(dummy_thumb)

    # Mock sliced output
    dummy_3mf = dummy_stl.with_suffix(".3mf")
    dummy_3mf.write_bytes(b"fake 3MF data")

    with (
        patch(
            "pipeline.stages.llm_interpret.interpret_request",
            new_callable=AsyncMock,
            return_value=mock_interpret_result,
        ),
        patch(
            "pipeline.stages.mesh_generate.run",
            new_callable=AsyncMock,
        ) as mock_mesh_run,
        patch(
            "pipeline.stages.slice._run_local",
            new_callable=AsyncMock,
        ) as mock_local_slice,
        patch(
            "pipeline.stages.print_job._send_via_ftp",
            new_callable=AsyncMock,
        ),
        patch(
            "pipeline.stages.print_job._monitor_via_mqtt",
            new_callable=AsyncMock,
        ),
    ):
        # Mock the mesh_generate.run to populate artifacts
        async def _fake_mesh_run(job, settings, progress_callback=None):
            job.advance(JobStage.GENERATING)
            job.artifacts.meshy_task_id = "abc123"
            job.artifacts.model_local_path = str(dummy_stl)
            job.artifacts.thumbnail_local_path = str(dummy_thumb)
            job.artifacts.model_format = "stl"
            job.advance(JobStage.AWAITING_MODEL_APPROVAL)
            summary = "🎨 *3D Model Generated!*"
            return summary, str(dummy_thumb)

        mock_mesh_run.side_effect = _fake_mesh_run

        # Mock the local slice stage to populate artifacts
        async def _fake_local_slice(job, settings):
            job.artifacts.sliced_file_path = str(dummy_3mf)
            job.artifacts.estimated_print_time = "1h 45m 20s"
            job.artifacts.estimated_filament_g = 32.5
            job.advance(JobStage.AWAITING_SLICE_APPROVAL)
            return (
                "✂️ *Slicing Complete!*\n\n"
                "**Output:** `abc123.3mf`\n"
                "**Est. print time:** 1h 45m 20s\n"
                "**Filament:** 32.5g\n\n"
                "Approve to start printing."
            )

        mock_local_slice.side_effect = _fake_local_slice

        job = orch.create_job(
            user_id=123,
            chat_id=456,
            raw_request="please 3D print a cinderella",
        )

        await orch.run_pipeline(job)

    # Verify job completed successfully
    assert job.stage == JobStage.DONE, f"Expected DONE, got {job.stage.value}: {job.error}"
    assert job.error is None

    # Verify artifacts were populated
    assert job.artifacts.enriched_prompt == "A detailed Cinderella figurine, standing pose"
    assert job.artifacts.object_name == "Cinderella figurine"
    assert job.artifacts.model_local_path == str(dummy_stl)
    assert job.artifacts.meshy_task_id == "abc123"

    # Verify interactions happened
    assert orch.send_message.call_count >= 3
    # In local mode: interpret, model, slice (no transfer) = 3 approvals
    assert orch.request_approval.call_count == 3


@pytest.mark.asyncio
async def test_pipeline_rejection_at_llm_stage(orchestrator, settings):
    """Test that rejecting at LLM interpretation cancels the pipeline."""
    orch = orchestrator

    async def _reject(chat_id, job_id, text):
        await orch.resolve_approval(job_id, False)

    orch.request_approval = AsyncMock(side_effect=_reject)

    mock_result = MagicMock()
    mock_result.enriched_prompt = "test"
    mock_result.object_name = "test"
    mock_result.suggested_scale_mm = 100.0
    mock_result.suggested_material = "PLA"
    mock_result.art_style = "realistic"
    mock_result.orientation_notes = ""

    with patch(
        "pipeline.stages.llm_interpret.interpret_request",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        job = orch.create_job(user_id=123, chat_id=456, raw_request="test cube")
        await orch.run_pipeline(job)

    assert job.stage == JobStage.CANCELLED


@pytest.mark.asyncio
async def test_pipeline_handles_llm_failure(orchestrator, settings):
    """Test that an LLM API error results in FAILED state."""
    orch = orchestrator

    with patch(
        "pipeline.stages.llm_interpret.interpret_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("OpenAI API error"),
    ):
        job = orch.create_job(user_id=123, chat_id=456, raw_request="test cube")
        await orch.run_pipeline(job)

    assert job.stage == JobStage.FAILED
    assert "OpenAI API error" in job.error


@pytest.mark.asyncio
async def test_job_state_transitions_are_tracked(orchestrator, settings):
    """Test that history is populated through transitions."""
    job = PrintJob(user_id=123, chat_id=456, raw_request="test")
    job.advance(JobStage.INTERPRETING)
    job.advance(JobStage.AWAITING_INTERPRET_APPROVAL)
    job.advance(JobStage.CANCELLED)

    assert len(job.history) == 3
    assert job.history[0]["from"] == "pending"
    assert job.history[0]["to"] == "interpreting"
    assert job.history[2]["to"] == "cancelled"
