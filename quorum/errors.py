"""Quorum error types."""


class QuorumError(Exception):
    """Base class for all Quorum errors."""


class CouncilError(QuorumError):
    """All providers in an agent's fallback chain failed."""


class DataError(QuorumError):
    """A data adapter failed hard (adapters should normally degrade, not raise)."""


class ConfigError(QuorumError):
    """Invalid or missing configuration."""
