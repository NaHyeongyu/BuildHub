from app.schemas.events import PromptSubmittedPayload
from app.schemas.project_responses import ProjectPromptActivityResponse


def test_prompt_continuity_payload_and_activity_response_contract() -> None:
    payload = PromptSubmittedPayload(
        prompt="이어서 진행해줘",
        submission_context="during_output",
        delivery_mode="unknown",
        continuation_of="prompt-1",
        root_prompt_event_id="prompt-1",
    )

    response = ProjectPromptActivityResponse(
        continuation_of=payload.continuation_of,
        delivery_mode=payload.delivery_mode,
        file_changes=[],
        files_changed=0,
        id="prompt-2",
        model="codex",
        prompt=payload.prompt,
        root_prompt_event_id=payload.root_prompt_event_id,
        sequence=2,
        session_id="session-1",
        submission_context=payload.submission_context,
        submitted_at="2026-07-25T00:00:00+00:00",
    )

    assert response.submission_context == "during_output"
    assert response.continuation_of == "prompt-1"
    assert response.root_prompt_event_id == "prompt-1"
