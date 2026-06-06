"""Python 3.10 compatibility (RunPod pytorch images ship Python 3.10)."""

try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    from enum import Enum

    class StrEnum(str, Enum):
        """str + Enum backport."""

        pass
