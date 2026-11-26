"""Closed accounting fixtures. Heights model ordering, not consensus maturity."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from db import block_recovery as journal
from db.models import Address, UTXO


def hid(n):
    return "%064x" % n


def key(n, vout=0):
    return hid(n) + str(vout)


def transaction(n, kind, inputs, outputs):
    return {"txid": hid(n), "hash": hid(n + 1000), "type": kind,
            "vin": [{"txid": hid(t), "vout": i} for t, i in inputs] or [{"coinbase": "00"}],
            "vout": [{"n": i, "value": v, "scriptPubKey": {"addresses": [a]} if a else
                      {"type": "nulldata", "hex": "6a"}} for i, (a, v) in enumerate(outputs)]}


def alert():
    return transaction(20, "TX_ALERT", [(11, 0), (11, 1)], [("recipient", 9.75)])


def block(h, branch):
    if branch not in ("settle", "recover") or not 0 <= h <= 6:
        raise ValueError("unknown fixture block")
    rewards = {0: [("genesis", 175)], 1: [("vault", 6), ("vault", 4), ("other", 8), ("keep", 3)],
               2: [("alert-miner", 1)], 5: ([("miner", 1), ("alert-miner", 0.25)] if branch == "settle"
                                           else [("miner", 1.25)])}
    regular = [transaction(10 + h, "TX_NONVAULT", [], rewards.get(h, [(None, 0)]))]
    alerts = []
    if h == 2:
        # A historical verbose RPC can report the later outcome already.
        alerts.append(dict(alert(), status="CONFIRMED" if branch == "settle" else "RECOVERED"))
    if branch == "recover" and h == 3:
        alerts.append(transaction(21, "TX_ALERT", [(11, 2)], [("other-recipient", 7.75)]))
    if h == 5:
        regular.append(alert() if branch == "settle" else
                       transaction(22, "TX_RECOVERY", [(11, 2), (11, 1), (11, 0)], [("safe", 17.75)]))
    if h == 6:
        regular.append(transaction(23, "TX_NONVAULT", [(20 if branch == "settle" else 22, 0)],
                                   [("sweep", 9.75 if branch == "settle" else 17.75)]))
    return {"height": h, "hash": hid(100 + h), "previousblockhash": hid(99 + h) if h else hid(0),
            "time": 1700000000 + h * 600, "difficulty": 1.0, "tx": regular, "atx": alerts}


def expected(h, branch):
    """Literal settled ownership and complete UTXO inventory, including locks."""
    if not h:
        return {}, {}
    first = {"txid": hid(20), "height": 2, "input_count": 2} if h >= 2 else None
    second = {"txid": hid(21), "height": 3, "input_count": 1} if branch == "recover" and h >= 3 else None
    utxos = {key(11): ("vault", 6, first), key(11, 1): ("vault", 4, first),
             key(11, 2): ("other", 8, second), key(11, 3): ("keep", 3, None)}
    balances = {"vault": 10, "other": 8, "keep": 3}
    if h >= 2:
        utxos[key(12)] = ("alert-miner", 1, None)
        balances["alert-miner"] = 1
    if h >= 5:
        del utxos[key(11)], utxos[key(11, 1)]
        balances["vault"] = 0
        if branch == "recover":
            del utxos[key(11, 2)]
            balances["other"] = 0
        n, dest, value = (20, "recipient", 9.75) if branch == "settle" else (22, "safe", 17.75)
        balances["miner"] = 1 if branch == "settle" else 1.25
        utxos[key(15)] = ("miner", balances["miner"], None)
        if branch == "settle":
            utxos[key(15, 1)] = ("alert-miner", 0.25, None)
            balances["alert-miner"] = 1.25
        utxos[key(n) if h == 5 else key(23)] = (dest if h == 5 else "sweep", value, None)
        balances[dest] = value if h == 5 else 0
        if h == 6:
            balances["sweep"] = value
    return utxos, balances


def inventory(utxos, addresses):
    return ({u["_id"]: (u["address"], u["value"], u.get("pending_alert")) for u in utxos},
            {a["_id"]: a["balance"] for a in addresses})


class VaultPlannerTest(unittest.TestCase):
    def setUp(self):
        self.documents = {UTXO._get_collection_name(): {}, Address._get_collection_name(): {}}
        self.tip = None

    def plan(self, raw):
        def read(name, keys):
            rows = self.documents[name]
            return {k: copy.deepcopy(rows[k]) for k in keys if k in rows}
        with patch.object(journal, "load", side_effect=read):
            return journal.plan(raw, self.tip)

    def advance(self, h, branch):
        body = self.plan(block(h, branch))
        for model, field in [(UTXO, "utxos"), (Address, "addresses")]:
            rows = self.documents[model._get_collection_name()]
            for edit in body[field]:
                k = edit["key"]
                self.assertEqual(rows.get(k), edit["before"])
                if edit["after"] is None:
                    del rows[k]
                else:
                    rows[k] = copy.deepcopy(edit["after"])
        self.tip = {"height": h, "_id": body["block"]["_id"]}
        actual = inventory(*(self.documents[m._get_collection_name()].values() for m in (UTXO, Address)))
        self.assertEqual(actual, expected(h, branch))

    def test_delayed_settlement_credits_recipient_and_fee_once(self):
        for h in range(7):
            self.advance(h, "settle")

    def test_recovery_closes_two_alert_groups_without_crediting_proposed_outputs(self):
        for h in range(7):
            self.advance(h, "recover")

    def test_locked_inputs_require_the_complete_matching_groups(self):
        for h in range(5):
            self.advance(h, "recover")
        for kind, n, inputs in [
                ("TX_NONVAULT", 30, [(11, 0)]), ("TX_INSTANT", 31, [(11, 2)]),
                ("TX_RECOVERY", 32, [(11, 0), (11, 2)]),
                ("TX_RECOVERY", 33, [(11, 0), (11, 1), (11, 3)]),
                ("TX_ALERT", 34, [(11, 0), (11, 1)]), ("TX_ALERT", 20, [(11, 0)])]:
            with self.subTest(kind=kind, n=n), self.assertRaises(ValueError):
                raw = block(5, "recover")
                raw["tx"][1] = transaction(n, kind, inputs, [("bad", 1)])
                self.plan(raw)

    def test_missing_alert_list_is_not_an_empty_alert_list(self):
        raw = block(0, "settle")
        del raw["atx"]
        with self.assertRaises(ValueError):
            self.plan(raw)

    def test_regular_output_can_be_reserved_later_in_the_same_block(self):
        for h in (0, 1):
            self.advance(h, "settle")
        raw = block(2, "settle")
        raw["tx"].append(transaction(30, "TX_NONVAULT", [(11, 3)], [("fresh-vault", 3)]))
        raw["atx"] = [transaction(31, "TX_ALERT", [(30, 0)], [("later", 2.75)])]
        body = self.plan(raw)
        after = {e["key"]: e["after"] for e in body["utxos"]}
        self.assertEqual(after[key(30)]["pending_alert"], {"txid": hid(31), "height": 2, "input_count": 1})
        self.assertEqual((after[key(30)]["address"], after[key(30)]["value"]), ("fresh-vault", 3))
        self.assertIsNone(after[key(11, 3)])
        self.assertNotIn(key(31), after)
        self.assertEqual({e["key"]: e["after"]["balance"] for e in body["addresses"]},
                         {"alert-miner": 1, "fresh-vault": 3, "keep": 0})


if __name__ == "__main__":
    unittest.main()
