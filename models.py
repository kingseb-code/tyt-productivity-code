import os

# Model routing — assign the right model to the right task
# Change these to adjust cost vs quality per task type

MODEL_ORCHESTRATOR = "claude-opus-4-6"          # complex decisions, SOUL logic
MODEL_WORKER = "claude-haiku-4-5-20251001"       # routine tasks: briefings, summaries
MODEL_DRAFTER = "claude-sonnet-4-6"             # composing replies, medium complexity

def get_model(role: str) -> str:
    """Return the model for a given role. Falls back to worker if unknown."""
    return {
        "orchestrator": MODEL_ORCHESTRATOR,
        "worker": MODEL_WORKER,
        "drafter": MODEL_DRAFTER,
    }.get(role, MODEL_WORKER)
