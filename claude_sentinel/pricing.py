"""Pricing registry and cost estimator for Anthropic and partner models."""
from typing import Dict, Tuple

# Rates per 1,000,000 tokens (USD)
# Format: (input_price, output_price, cache_write_price, cache_read_price)
MODEL_PRICING: Dict[str, Tuple[float, float, float, float]] = {
    # Claude 3.5 Sonnet
    "claude-3-5-sonnet-20241022": (3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet-20240620": (3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet": (3.00, 15.00, 3.75, 0.30),

    # Claude 3.5 Haiku
    "claude-3-5-haiku-20241022": (0.80, 4.00, 1.00, 0.08),
    "claude-3-5-haiku": (0.80, 4.00, 1.00, 0.08),

    # Claude 3 Opus
    "claude-3-opus-20240229": (15.00, 75.00, 18.75, 1.50),
    "claude-3-opus": (15.00, 75.00, 18.75, 1.50),

    # Claude 3 Sonnet
    "claude-3-sonnet-20240229": (3.00, 15.00, 3.75, 0.30),

    # Claude 3 Haiku
    "claude-3-haiku-20240307": (0.25, 1.25, 0.30, 0.03),

    # Next-Gen / Claude Code aliases
    "claude-opus-5[1m]": (15.00, 75.00, 18.75, 1.50),
    "claude-3-7-sonnet": (3.00, 15.00, 3.75, 0.30),
}

DEFAULT_FALLBACK_PRICING = (3.00, 15.00, 3.75, 0.30)  # Assume standard Sonnet tier


def get_model_pricing(model_name: str) -> Tuple[float, float, float, float]:
    """Resolve token pricing for a given model identifier."""
    if not model_name:
        return DEFAULT_FALLBACK_PRICING

    model_lower = model_name.lower().strip()
    if model_lower in MODEL_PRICING:
        return MODEL_PRICING[model_lower]

    # Partial / substring matching
    if "haiku" in model_lower:
        return MODEL_PRICING["claude-3-5-haiku-20241022"]
    if "opus" in model_lower:
        return MODEL_PRICING["claude-3-opus-20240229"]
    if "sonnet" in model_lower:
        return MODEL_PRICING["claude-3-5-sonnet-20241022"]

    # Free openrouter / test models
    if "free" in model_lower:
        return (0.0, 0.0, 0.0, 0.0)

    return DEFAULT_FALLBACK_PRICING


def calculate_cost_usd(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """
    Calculate the estimated cost in USD for the given token metrics.
    Prices are per million tokens.
    """
    in_rate, out_rate, write_rate, read_rate = get_model_pricing(model_name)

    cost = (
        (input_tokens / 1_000_000.0) * in_rate
        + (output_tokens / 1_000_000.0) * out_rate
        + (cache_creation_tokens / 1_000_000.0) * write_rate
        + (cache_read_tokens / 1_000_000.0) * read_rate
    )
    return round(cost, 6)
