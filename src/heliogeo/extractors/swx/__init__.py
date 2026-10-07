"""Extractor для API 503.ifz.ru/swx."""

from heliogeo.extractors.swx.availability import AvailabilityReport
from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.extractors.swx.extractor import SwxExtractor, SwxExtractorError
from heliogeo.extractors.swx.response import (
    SourceRef,
    SwxDataResponse,
    SwxResponseError,
    SwxTextResponse,
    TextRef,
)

__all__ = [
    "AvailabilityReport",
    "SourceRef",
    "SwxClient",
    "SwxClientError",
    "SwxDataResponse",
    "SwxExtractor",
    "SwxExtractorError",
    "SwxResponseError",
    "SwxTextResponse",
    "TextRef",
]
