from untisian import config
from untisian.untis.base import UntisProvider


def get_provider() -> UntisProvider:
    """UNTIS_PROVIDER=real|mock (default mock, so nothing logs in by accident)."""
    if config.get("UNTIS_PROVIDER", "mock") == "real":
        from untisian.untis.real import RealUntisProvider
        return RealUntisProvider()
    from untisian.untis.mock import MockUntisProvider
    return MockUntisProvider()
