"""Small logging helper kept local to the standalone package."""

import logging


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
