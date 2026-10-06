# feature_flag

Evaluate feature flags with percentage rollout and per-identifier overrides.

```python
from feature_flag import Flag, FlagSet

fs = FlagSet([
    Flag("checkout-v2", percentage=25, overrides={"internal-staff": True}),
])

if fs.evaluate("checkout-v2", "user-12345"):
    ...  # show new checkout
```

## Why

Most feature-flag libraries pull in a client, a network layer, and a
dashboard.  This one does none of that: it is a single-file evaluator for
code that already has its own config source (a file, an env var, a database)
and just needs the rollout math done correctly.

The trade-off is that there is no state management here.  You construct
`Flag` objects from whatever config you have, evaluate, and throw them away.
If you need live updates, rebuild the `FlagSet`.

## Edge cases worth knowing

- **Unknown flags return `False`.**  `FlagSet.evaluate` never raises for a
  missing key; it returns `False`.  This is deliberate — a typo in a flag
  name should not take down a request.
- **Disabled flags ignore everything.**  When `enabled=False`, overrides and
  percentage are both skipped.  This is the kill-switch path.
- **Overrides bypass the percentage but not the master switch.**  An
  override of `True` on a disabled flag still evaluates `False`.
- **Rollout is monotonic.**  Raising the percentage from 30 to 60 never
  turns off a user who was already on at 30.  This holds because the bucket
  is `hash(flag_key + ':' + identifier) % 10000` compared against
  `percentage * 100`.

## Exported names

- `Flag(key, *, enabled=True, percentage=100, overrides=None)` — single
  flag definition.
- `Flag.evaluate(identifier) -> bool` — evaluate for one identifier.
- `Flag.to_dict() / Flag.from_dict(data)` — JSON-friendly serialisation.
- `FlagSet(flags=None)` — collection with lookup.
- `FlagSet.add(flag)` — add a flag; rejects duplicate keys.
- `FlagSet.get(key) -> Flag | None` — direct lookup.
- `FlagSet.evaluate(key, identifier) -> bool` — evaluate by key; `False` if
  the key is absent.
- `FlagSet.to_json() / FlagSet.from_json(data)` — serialise the whole set.
- `evaluate_flag(flag, identifier) -> bool` — convenience wrapper for a
  single `Flag`.

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

