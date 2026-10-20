from clipforge.queue.errors import TransientError


class ModelUnavailable(TransientError):
    """The model is missing or keeps failing; callers may switch to a fallback model."""
