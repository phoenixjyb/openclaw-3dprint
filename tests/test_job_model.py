"""Tests for the PrintJob state machine."""

from pipeline.models.job import JobStage, PrintJob


def test_create_job():
    job = PrintJob(raw_request="print a cinderella")
    assert job.stage == JobStage.PENDING
    assert job.raw_request == "print a cinderella"
    assert not job.is_terminal


def test_valid_transitions():
    job = PrintJob(raw_request="test")
    job.advance(JobStage.INTERPRETING)
    assert job.stage == JobStage.INTERPRETING

    job.advance(JobStage.AWAITING_INTERPRET_APPROVAL)
    assert job.stage == JobStage.AWAITING_INTERPRET_APPROVAL
    assert job.is_awaiting_approval

    job.advance(JobStage.GENERATING)
    assert job.stage == JobStage.GENERATING

    job.advance(JobStage.AWAITING_MODEL_APPROVAL)
    job.advance(JobStage.TRANSFERRING)
    job.advance(JobStage.AWAITING_TRANSFER_APPROVAL)
    job.advance(JobStage.SLICING)
    job.advance(JobStage.AWAITING_SLICE_APPROVAL)
    job.advance(JobStage.PRINTING)
    job.advance(JobStage.DONE)
    assert job.is_terminal
    assert len(job.history) == 10


def test_invalid_transition():
    job = PrintJob(raw_request="test")
    try:
        job.advance(JobStage.PRINTING)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Invalid transition" in str(e)


def test_cancel_from_approval():
    job = PrintJob(raw_request="test")
    job.advance(JobStage.INTERPRETING)
    job.advance(JobStage.AWAITING_INTERPRET_APPROVAL)
    job.advance(JobStage.CANCELLED)
    assert job.is_terminal


def test_fail_from_stage():
    job = PrintJob(raw_request="test")
    job.advance(JobStage.INTERPRETING)
    job.advance(JobStage.FAILED, error="API error")
    assert job.is_terminal
    assert job.error == "API error"


def test_summary():
    job = PrintJob(raw_request="a cinderella figurine")
    job.artifacts.object_name = "Cinderella"
    job.artifacts.estimated_print_time = "2h 30m"
    s = job.summary()
    assert "Cinderella" in s
    assert "2h 30m" in s
