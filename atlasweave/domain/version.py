"""Semantic version ordering without runtime dependencies."""

import re
from dataclasses import dataclass
from functools import total_ordering

from atlasweave.domain.values import ModelError

_PATTERN = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    release: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _PATTERN.fullmatch(value) if isinstance(value, str) else None
        if match is None:
            raise ModelError("format_version", "必须是有效的语义版本")
        prerelease = tuple(match[4].split(".")) if match[4] else ()
        if any(item.isdigit() and len(item) > 1 and item.startswith("0") for item in prerelease):
            raise ModelError("format_version", "预发布数字版本不得有前导零")
        return cls(tuple(int(match[i]) for i in (1, 2, 3)), prerelease)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.release == other.release and self.prerelease == other.prerelease

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        if self.release != other.release:
            return self.release < other.release
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)
