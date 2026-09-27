"""Core feature-flag evaluation logic.

Design decisions
----------------

* Percentage rollout uses a stable hash of ``flag_key + ':' + identifier``.
  This means the same user is always in the same bucket for a given flag, so
  increasing a rollout percentage from 10 to 20 never re-evaluates users who
  were already in the first 10 percent — they stay on, and the next slice
  joins them.  We use ``hashlib.sha256`` (truncated to 32 bits) rather than
  Python's built-in ``hash()`` because the latter is salted per process and
  would produce different buckets across restarts.

* Overrides are evaluated **before** the percentage rollout.  This lets
  operators force a flag on or off for a specific identifier regardless of
  where that identifier would naturally fall in the rollout.

* ``FlagSet`` is intentionally a plain class, not a dict subclass, so that
  the lookup method can return a clear ``False`` for unknown flags instead
  of raising ``KeyError``.  A missing flag is a common production condition
  (stale config, typo) and treating it as "off" is safer than crashing the
  call site.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


_HASH_MASK = 0xFFFFFFFF  # 32-bit mask
_BUCKET_MAX = 10000  # 0–9999, gives 0.01% granularity


def _bucket(flag_key: str, identifier: str) -> int:
    """Return a deterministic bucket in ``[0, 10000)`` for *identifier*.

    The bucket is derived from the first four bytes of
    ``sha256(flag_key + ':' + identifier)`` interpreted big-endian.
    Including the flag key in the hash means that two flags with the same
    rollout percentage will not bucket the same set of users, which avoids
    correlated rollouts.
    """
    digest = hashlib.sha256(f"{flag_key}:{identifier}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") & _HASH_MASK
    return value % _BUCKET_MAX


class Flag:
    """A single feature flag definition.

    Parameters
    ----------
    key:
        Stable name for the flag.  Used both for lookups and as part of the
        rollout hash, so changing it will reshuffle every user's bucket.
    enabled:
        Master switch.  When ``False`` the flag evaluates ``False`` for
        everyone, ignoring overrides and rollout.
    percentage:
        Integer 0–100 inclusive.  ``0`` means nobody, ``100`` means everyone.
    overrides:
        Mapping of ``identifier -> bool``.  An override forces the flag
        on (``True``) or off (``False``) for that identifier, bypassing the
        percentage rollout.  Overrides are still subject to the master
        ``enabled`` switch.
    """

    __slots__ = ("key", "enabled", "percentage", "overrides")

    def __init__(
        self,
        key: str,
        *,
        enabled: bool = True,
        percentage: int = 100,
        overrides: Optional[Dict[str, bool]] = None,
    ) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        if not isinstance(percentage, int) or isinstance(percentage, bool):
            raise TypeError("percentage must be int")
        if not 0 <= percentage <= 100:
            raise ValueError("percentage must be between 0 and 100")
        if overrides is not None:
            for k, v in overrides.items():
                if not isinstance(k, str):
                    raise TypeError("override keys must be strings")
                if not isinstance(v, bool):
                    raise TypeError("override values must be bool")
        self.key = key
        self.enabled = enabled
        self.percentage = percentage
        self.overrides: Dict[str, bool] = dict(overrides) if overrides else {}

    def evaluate(self, identifier: str) -> bool:
        """Evaluate this flag for *identifier*.

        Returns ``False`` if the flag is disabled.  Otherwise overrides take
        priority, then percentage rollout.
        """
        if not self.enabled:
            return False
        if identifier in self.overrides:
            return self.overrides[identifier]
        if self.percentage == 100:
            return True
        if self.percentage == 0:
            return False
        return _bucket(self.key, identifier) < self.percentage * 100

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON."""
        return {
            "key": self.key,
            "enabled": self.enabled,
            "percentage": self.percentage,
            "overrides": dict(self.overrides),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Flag":
        """Reconstruct a :class:`Flag` from :meth:`to_dict` output."""
        return cls(
            key=data["key"],
            enabled=data["enabled"],
            percentage=data["percentage"],
            overrides=data.get("overrides", {}),
        )

    def __repr__(self) -> str:
        return (
            f"Flag(key={self.key!r}, enabled={self.enabled!r}, "
            f"percentage={self.percentage!r}, overrides={self.overrides!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Flag):
            return NotImplemented
        return (
            self.key == other.key
            and self.enabled == other.enabled
            and self.percentage == other.percentage
            and self.overrides == other.overrides
        )


class FlagSet:
    """A collection of :class:`Flag` objects with lookup by key.

    Unknown keys evaluate to ``False`` rather than raising, because a missing
    flag in production should degrade gracefully.
    """

    __slots__ = ("_flags",)

    def __init__(self, flags: Optional[List[Flag]] = None) -> None:
        self._flags: Dict[str, Flag] = {}
        if flags:
            for f in flags:
                if not isinstance(f, Flag):
                    raise TypeError("flags must contain Flag instances")
                if f.key in self._flags:
                    raise ValueError(f"duplicate flag key: {f.key!r}")
                self._flags[f.key] = f

    def add(self, flag: Flag) -> None:
        """Add *flag*.  Raises ``ValueError`` on a duplicate key."""
        if not isinstance(flag, Flag):
            raise TypeError("flag must be a Flag instance")
        if flag.key in self._flags:
            raise ValueError(f"duplicate flag key: {flag.key!r}")
        self._flags[flag.key] = flag

    def get(self, key: str) -> Optional[Flag]:
        """Return the :class:`Flag` for *key*, or ``None`` if absent."""
        return self._flags.get(key)

    def evaluate(self, key: str, identifier: str) -> bool:
        """Evaluate flag *key* for *identifier*.

        Returns ``False`` if the flag is not present.
        """
        flag = self._flags.get(key)
        if flag is None:
            return False
        return flag.evaluate(identifier)

    def to_json(self) -> str:
        """Serialise the whole set to a JSON string."""
        return json.dumps([f.to_dict() for f in self._flags.values()])

    @classmethod
    def from_json(cls, data: str) -> "FlagSet":
        """Reconstruct a :class:`FlagSet` from :meth:`to_json` output."""
        raw = json.loads(data)
        return cls([Flag.from_dict(item) for item in raw])

    def __len__(self) -> int:
        return len(self._flags)

    def __contains__(self, key: object) -> bool:
        return key in self._flags

    def __repr__(self) -> str:
        return f"FlagSet({list(self._flags.values())!r})"


def evaluate_flag(
    flag: Flag, identifier: str
) -> bool:
    """Convenience function: evaluate a single :class:`Flag`.

    Equivalent to ``flag.evaluate(identifier)``; provided so callers who only
    need one flag can avoid building a :class:`FlagSet`.
    """
    return flag.evaluate(identifier)
