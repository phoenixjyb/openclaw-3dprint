"""PrintJob — state model for a single print pipeline run."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobStage(str, enum.Enum):
    """Pipeline stages a job moves through."""

    PENDING = "pending"
    INTERPRETING = "interpreting"
    AWAITING_INTERPRET_APPROVAL = "awaiting_interpret_approval"
    GENERATING = "generating"
    AWAITING_MODEL_APPROVAL = "awaiting_model_approval"
    TRANSFERRING = "transferring"
    AWAITING_TRANSFER_APPROVAL = "awaiting_transfer_approval"
    SLICING = "slicing"
    AWAITING_SLICE_APPROVAL = "awaiting_slice_approval"
    PRINTING = "printing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid stage transitions
_TRANSITIONS: dict[JobStage, list[JobStage]] = {
    JobStage.PENDING: [JobStage.INTERPRETING, JobStage.CANCELLED],
    JobStage.INTERPRETING: [JobStage.AWAITING_INTERPRET_APPROVAL, JobStage.FAILED],
    JobStage.AWAITING_INTERPRET_APPROVAL: [
        JobStage.GENERATING, JobStage.INTERPRETING, JobStage.CANCELLED,
    ],
    JobStage.GENERATING: [JobStage.AWAITING_MODEL_APPROVAL, JobStage.FAILED],
    JobStage.AWAITING_MODEL_APPROVAL: [
        JobStage.TRANSFERRING, JobStage.SLICING, JobStage.GENERATING, JobStage.CANCELLED,
    ],
    JobStage.TRANSFERRING: [JobStage.AWAITING_TRANSFER_APPROVAL, JobStage.FAILED],
    JobStage.AWAITING_TRANSFER_APPROVAL: [JobStage.SLICING, JobStage.CANCELLED],
    JobStage.SLICING: [JobStage.AWAITING_SLICE_APPROVAL, JobStage.FAILED],
    JobStage.AWAITING_SLICE_APPROVAL: [JobStage.PRINTING, JobStage.CANCELLED],
    JobStage.PRINTING: [JobStage.DONE, JobStage.FAILED],
}


class StageArtifact(BaseModel):
    """Artifacts produced by a single stage."""

    enriched_prompt: Optional[str] = None
    object_name: Optional[str] = None
    suggested_scale_mm: Optional[float] = None
    suggested_material: Optional[str] = None

    meshy_task_id: Optional[str] = None
    model_local_path: Optional[str] = None
    thumbnail_local_path: Optional[str] = None
    model_format: Optional[str] = None  # "stl", "glb", "obj"

    windows_remote_path: Optional[str] = None
    sliced_file_path: Optional[str] = None
    estimated_print_time: Optional[str] = None
    estimated_filament_g: Optional[float] = None

    print_progress_pct: Optional[float] = None
    print_eta: Optional[str] = None


class PrintJob(BaseModel):
    """Represents a single text-to-print pipeline run."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # User input
    user_id: int = 0
    chat_id: int = 0
    raw_request: str = ""

    # State
    stage: JobStage = JobStage.PENDING
    error: Optional[str] = None
    retry_count: int = 0

    # Accumulated artifacts from each stage
    artifacts: StageArtifact = Field(default_factory=StageArtifact)

    # History of stage transitions
    history: list[dict[str, Any]] = Field(default_factory=list)

    def advance(self, new_stage: JobStage, error: Optional[str] = None) -> None:
        """Transition to a new stage with validation."""
        allowed = _TRANSITIONS.get(self.stage, [])
        if new_stage not in allowed:
            raise ValueError(
                f"Invalid transition: {self.stage.value} → {new_stage.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        self.history.append({
            "from": self.stage.value,
            "to": new_stage.value,
            "at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        })
        self.stage = new_stage
        self.error = error
        self.updated_at = datetime.now(timezone.utc)

    @property
    def is_terminal(self) -> bool:
        return self.stage in (JobStage.DONE, JobStage.FAILED, JobStage.CANCELLED)

    @property
    def is_awaiting_approval(self) -> bool:
        return self.stage.value.startswith("awaiting_")

    def summary(self) -> str:
        lines = [
            f"🖨 Job `{self.id}` — **{self.stage.value}**",
            f"Request: _{self.raw_request}_",
        ]
        a = self.artifacts
        if a.enriched_prompt:
            lines.append(f"Enriched: {a.enriched_prompt[:120]}…")
        if a.object_name:
            lines.append(f"Object: {a.object_name}")
        if a.estimated_print_time:
            lines.append(f"Est. time: {a.estimated_print_time}")
        if a.estimated_filament_g:
            lines.append(f"Filament: {a.estimated_filament_g:.1f}g")
        if a.print_progress_pct is not None:
            lines.append(f"Progress: {a.print_progress_pct:.0f}%")
        if self.error:
            lines.append(f"⚠️ Error: {self.error}")
        return "\n".join(lines)
