import json
import unittest

from feature_flag import Flag, FlagSet, evaluate_flag


class TestFlagConstruction(unittest.TestCase):

    def test_defaults(self):
        f = Flag("checkout-v2")
        self.assertTrue(f.enabled)
        self.assertEqual(f.percentage, 100)
        self.assertEqual(f.overrides, {})

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            Flag("")

    def test_percentage_out_of_range(self):
        with self.assertRaises(ValueError):
            Flag("x", percentage=-1)
        with self.assertRaises(ValueError):
            Flag("x", percentage=101)

    def test_percentage_must_be_int_not_bool(self):
        with self.assertRaises(TypeError):
            Flag("x", percentage=True)

    def test_enabled_must_be_bool(self):
        with self.assertRaises(TypeError):
            Flag("x", enabled=1)

    def test_override_values_must_be_bool(self):
        with self.assertRaises(TypeError):
            Flag("x", overrides={"u": 1})

    def test_override_keys_must_be_strings(self):
        with self.assertRaises(TypeError):
            Flag("x", overrides={1: True})


class TestFlagEvaluate(unittest.TestCase):

    def test_disabled_flag_is_always_false(self):
        f = Flag("x", enabled=False, percentage=100, overrides={"u": True})
        self.assertFalse(f.evaluate("u"))

    def test_override_on_takes_priority(self):
        f = Flag("x", enabled=True, percentage=0, overrides={"u": True})
        self.assertTrue(f.evaluate("u"))

    def test_override_off_takes_priority(self):
        f = Flag("x", enabled=True, percentage=100, overrides={"u": False})
        self.assertFalse(f.evaluate("u"))

    def test_percentage_0_is_false_for_everyone(self):
        f = Flag("x", percentage=0)
        self.assertFalse(f.evaluate("alice"))
        self.assertFalse(f.evaluate("bob"))

    def test_percentage_100_is_true_for_everyone(self):
        f = Flag("x", percentage=100)
        self.assertTrue(f.evaluate("alice"))
        self.assertTrue(f.evaluate("bob"))

    def test_rollout_is_stable_per_identifier(self):
        f = Flag("x", percentage=50)
        first = f.evaluate("alice")
        second = f.evaluate("alice")
        self.assertEqual(first, second)

    def test_rollout_distribution_is_reasonable(self):
        # With 50% rollout on 1000 distinct identifiers we expect roughly 500
        # to be on.  We allow a wide band (400–600) because this is a hash
        # bucket test, not a RNG test, and we want determinism not tight
        # statistics.
        f = Flag("x", percentage=50)
        on = sum(1 for i in range(1000) if f.evaluate(f"user-{i}"))
        self.assertGreater(on, 400)
        self.assertLess(on, 600)

    def test_different_flags_bucket_differently(self):
        # Two flags with the same percentage should not bucket identically,
        # otherwise correlated rollouts would occur.
        f1 = Flag("flag-a", percentage=50)
        f2 = Flag("flag-b", percentage=50)
        diffs = sum(
            1
            for i in range(1000)
            if f1.evaluate(f"u-{i}") != f2.evaluate(f"u-{i}")
        )
        self.assertGreater(diffs, 100)

    def test_increasing_percentage_only_adds_users(self):
        # Users who were on at 30% must still be on at 60%.
        f_low = Flag("x", percentage=30)
        f_high = Flag("x", percentage=60)
        for i in range(500):
            ident = f"u-{i}"
            if f_low.evaluate(ident):
                self.assertTrue(
                    f_high.evaluate(ident),
                    f"user {ident} was on at 30% but off at 60%",
                )


class TestFlagSerialisation(unittest.TestCase):

    def test_roundtrip(self):
        f = Flag("x", enabled=True, percentage=42, overrides={"u": True})
        f2 = Flag.from_dict(f.to_dict())
        self.assertEqual(f, f2)

    def test_to_dict_is_json_serialisable(self):
        f = Flag("x", percentage=42, overrides={"u": False})
        s = json.dumps(f.to_dict())
        self.assertIn("x", s)


class TestFlagSet(unittest.TestCase):

    def test_unknown_key_evaluates_false(self):
        fs = FlagSet()
        self.assertFalse(fs.evaluate("missing", "alice"))

    def test_add_and_evaluate(self):
        fs = FlagSet([Flag("a", percentage=100)])
        self.assertTrue(fs.evaluate("a", "alice"))

    def test_duplicate_key_rejected_in_constructor(self):
        with self.assertRaises(ValueError):
            FlagSet([Flag("a"), Flag("a")])

    def test_duplicate_key_rejected_in_add(self):
        fs = FlagSet([Flag("a")])
        with self.assertRaises(ValueError):
            fs.add(Flag("a"))

    def test_add_rejects_non_flag(self):
        fs = FlagSet()
        with self.assertRaises(TypeError):
            fs.add("not a flag")

    def test_contains_and_len(self):
        fs = FlagSet([Flag("a"), Flag("b")])
        self.assertIn("a", fs)
        self.assertNotIn("z", fs)
        self.assertEqual(len(fs), 2)

    def test_get_returns_flag_or_none(self):
        fs = FlagSet([Flag("a")])
        self.assertIsInstance(fs.get("a"), Flag)
        self.assertIsNone(fs.get("z"))

    def test_json_roundtrip(self):
        fs = FlagSet([
            Flag("a", percentage=100),
            Flag("b", percentage=0, overrides={"u": True}),
        ])
        s = fs.to_json()
        fs2 = FlagSet.from_json(s)
        self.assertEqual(len(fs2), 2)
        self.assertTrue(fs2.evaluate("a", "anyone"))
        self.assertTrue(fs2.evaluate("b", "u"))
        self.assertFalse(fs2.evaluate("b", "other"))


class TestEvaluateFlagFunction(unittest.TestCase):

    def test_matches_method(self):
        f = Flag("x", percentage=100)
        self.assertEqual(evaluate_flag(f, "alice"), f.evaluate("alice"))


if __name__ == "__main__":
    unittest.main()
