MAX_EXPONENT = 10


def backoff_seconds(*, base: float, attempt: int, limit: float) -> float:
    """`base` doubled `attempt` times, never above `limit`. The exponent stops at
    `MAX_EXPONENT`: a refund is retried for ever, and a float overflows near 2**1024.
    """
    return min(base * 2 ** min(attempt, MAX_EXPONENT), limit)
