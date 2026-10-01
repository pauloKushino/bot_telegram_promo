import sys

from loguru import logger

from deals.config import settings


def setup_logging() -> None:
    """Configura o loguru. Chamar uma vez na entrada de cada processo."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL.upper(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "{name}:{function}:{line} - {message}",
    )
