"""Extractor для API 503.ifz.ru/swx."""

from heliogeo.extractors.swx.client import SwxClient, SwxClientError

__all__ = ["SwxClient", "SwxClientError"]
