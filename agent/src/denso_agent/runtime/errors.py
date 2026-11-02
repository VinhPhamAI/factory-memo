"""Stable failures safe to expose through the public stream."""


class AgentError(Exception):
    """Carry a sanitized failure code, explanation and manual-retry indication."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        """Describe an expected failure without retaining private provider details.

        Args:
            code: Stable public error identifier.
            message: Explanation safe for service callers.
            retryable: Whether a fresh manual request may be attempted.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
