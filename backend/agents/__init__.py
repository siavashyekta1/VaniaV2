# agents/__init__.py
import logging

# Configure package-level logger
logger = logging.getLogger(__name__)
logger.debug("📦 [Agents] Package initialized.")

__all__ = ["router"]


def __getattr__(name):
    if name == "router":
        from .routes import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
