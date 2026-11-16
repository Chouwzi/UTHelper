"""
BS4 parser selection with fallback.
Uses lxml when available (faster), falls back to html.parser (stdlib) on Android/etc.
"""
import logging

logger = logging.getLogger(__name__)

try:
    import lxml  # noqa: F401
    BS4_PARSER = "lxml"
except ImportError:
    BS4_PARSER = "html.parser"
    logger.info("lxml not available, using html.parser fallback")
