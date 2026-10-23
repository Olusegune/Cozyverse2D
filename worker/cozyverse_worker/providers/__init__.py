from .base import (Capabilities, CostEstimate, GenerateRequest, ImageProvider, ImageResult, ModelInfo,
                   ProviderError)
from .google import GoogleProvider
from .kie import KieProvider
from .mock import MockProvider
from .openai import OpenAIProvider


def build_registry(**kw) -> dict[str, ImageProvider]:
    return {p.id: p for p in (MockProvider(**kw), GoogleProvider(**kw), OpenAIProvider(**kw), KieProvider(**kw))}


__all__ = ["Capabilities", "CostEstimate", "GenerateRequest", "ImageProvider", "ImageResult", "ModelInfo",
           "ProviderError", "build_registry", "MockProvider", "GoogleProvider", "OpenAIProvider", "KieProvider"]
