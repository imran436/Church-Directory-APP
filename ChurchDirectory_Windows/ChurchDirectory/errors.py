"""
errors.py — Custom exception hierarchy for the Church Directory Generator.

All exceptions inherit from DirectoryError so the orchestrator can
catch expected failures distinctly from unexpected ones.
"""


class DirectoryError(Exception):
    """Base class for all expected application errors."""
    def __init__(self, message: str, user_message: str = ""):
        super().__init__(message)
        self.user_message = user_message or message


class CredentialsNotFoundError(DirectoryError):
    """No credentials found in keychain or fallback store."""
    def __init__(self):
        super().__init__(
            "No credentials found in credential store.",
            "Setup required — please enter your Planning Center App ID and Personal Access Token."
        )


class CredentialsInvalidError(DirectoryError):
    """Credentials exist but were rejected by Planning Center (HTTP 401)."""
    def __init__(self):
        super().__init__(
            "Planning Center returned HTTP 401 — credentials rejected.",
            "Your Planning Center credentials are no longer valid. Please re-enter your Personal Access Token."
        )


class ListNotFoundError(DirectoryError):
    """The configured list ID does not exist or is not accessible (HTTP 404)."""
    def __init__(self, list_id: str):
        super().__init__(
            f"Planning Center list {list_id!r} returned HTTP 404.",
            f"The member list could not be found (ID: {list_id}). Please check your list ID in setup."
        )


class RateLimitError(DirectoryError):
    """HTTP 429 — rate limit exceeded after all retries."""
    def __init__(self):
        super().__init__(
            "Planning Center rate limit exceeded after maximum retries.",
            "Planning Center is rate-limiting requests. Please wait a few minutes and try again."
        )


class NetworkError(DirectoryError):
    """Could not reach Planning Center at all."""
    def __init__(self, detail: str = ""):
        super().__init__(
            f"Network error: {detail}",
            "Cannot connect to Planning Center. Please check your internet connection and try again."
        )


class ConfigError(DirectoryError):
    """Missing or invalid configuration."""
    def __init__(self, detail: str):
        super().__init__(
            f"Configuration error: {detail}",
            f"Configuration problem: {detail}. Please contact your administrator."
        )


class PDFRenderError(DirectoryError):
    """PDF generation failed."""
    def __init__(self, detail: str):
        super().__init__(
            f"PDF render error: {detail}",
            "Could not generate the PDF. Full details have been saved to the run log."
        )


class OutputWriteError(DirectoryError):
    """Could not write output to the chosen folder."""
    def __init__(self, path: str, detail: str):
        super().__init__(
            f"Cannot write to {path!r}: {detail}",
            f"Cannot save files to the chosen folder. Please choose a different location."
        )


class ZeroMembersError(DirectoryError):
    """API returned zero members — unusual, warn staff."""
    def __init__(self, list_id: str):
        super().__init__(
            f"Zero members returned from list {list_id!r}.",
            "No members were found in the selected list. Please check your Planning Center list and try again."
        )
