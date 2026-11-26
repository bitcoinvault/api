"""Closed accounting fixtures. Heights model ordering, not consensus maturity."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from db import block_recovery as journal
from db.models import Address, UTXO


REGTEST = "01cead69f2b51e214e1c2cd50e3744428cae526db87b2ff8f32489ff801d0f1d"
MAINNET = "0000000028ce26975b32feda3d75ac3fe10372f75062366cfba4e934dcc6a48b"
TESTNET = "0000000092bdcddd8bcf515ef5820dfc05dafaf708d316b35cd841c59f498637"


def hid(n):
    return "%064x" % n


def block_id(h):
    return REGTEST if h == 0 else hid(100 + h)


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
    return {"height": h, "hash": block_id(h), "previousblockhash": block_id(h-1) if h else hid(0),
            "alertmerkleroot": alerts[0]["txid"] if alerts else hid(0),
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

    def read(self, name, keys):
        rows = self.documents[name]
        return {k: copy.deepcopy(rows[k]) for k in keys if k in rows}

    def plan(self, raw):
        # The short fixture models already-initialized Core. Test the actual boundary separately.
        with patch.object(journal, "load", side_effect=self.read), patch.object(journal, "ALERT_INIT_WINDOW", 0):
            return journal.plan(raw, self.tip, REGTEST)

    def test_real_core_initialization_boundary_only_reserves_above_144(self):
        self.advance(0, "settle")
        self.advance(1, "settle")
        for height, count in ((116, 0), (144, 0), (145, 2)):
            raw = block(2, "settle")
            raw["height"] = height
            tip = {"_id": raw["previousblockhash"], "height": height-1}
            with self.subTest(height=height), patch.object(journal, "load", side_effect=self.read):
                body = journal.plan(raw, tip, REGTEST)
                marks = [row["after"]["pending_alert"] for row in body["utxos"]
                         if row["after"] and "pending_alert" in row["after"]]
                self.assertEqual([m["height"] for m in marks], [height] * count)
                self.assertEqual(len(body["block"]["atx"]), 1)

    def test_regular_vault_labels_change_semantics_only_at_network_activation(self):
        self.advance(0, "settle")
        self.advance(1, "settle")
        for genesis, activation in ((MAINNET, 58420), (TESTNET, 92960)):
            for offset in (-1, 0):
                for kind in ("TX_NONVAULT", "TX_ALERT", "TX_RECOVERY"):
                    raw = block(5, "settle")
                    raw["height"] = activation + offset
                    raw["tx"][1]["type"] = kind
                    if offset == -1:
                        # The coinbase's second push has no commitment semantics before activation.
                        raw["alertmerkleroot"] = hid(1234)
                    tip = {"height": raw["height"]-1, "_id": raw["previousblockhash"]}
                    with self.subTest(genesis=genesis, offset=offset, kind=kind), \
                            patch.object(journal, "load", side_effect=self.read):
                        if offset == 0 and kind != "TX_NONVAULT":
                            with self.assertRaisesRegex(ValueError, "without an indexed reservation"):
                                journal.plan(raw, tip, genesis)
                        else:
                            body = journal.plan(raw, tip, genesis)
                            balances = {e["key"]: e["after"]["balance"] for e in body["addresses"]}
                            self.assertEqual((balances["vault"], balances["recipient"]), (0, 9.75))

    def test_activation_requires_commitment_even_for_empty_or_uninitialized_list(self):
        for genesis, height in ((MAINNET, 58420), (TESTNET, 92960), (REGTEST, 1), (REGTEST, 144)):
            for template in (1, 2):
                raw = block(template, "settle")
                raw["height"] = height
                tip = {"height": height-1, "_id": genesis if height == 1 else raw["previousblockhash"]}
                raw["previousblockhash"] = tip["_id"]
                for root in (None, hid(99)):
                    candidate = dict(raw)
                    if root is None:
                        del candidate["alertmerkleroot"]
                    else:
                        candidate["alertmerkleroot"] = root
                    with self.subTest(genesis=genesis, height=height, template=template, root=root), \
                            patch.object(journal, "load") as read, self.assertRaisesRegex(ValueError, "commitment"):
                        try:
                            journal.plan(candidate, tip, genesis)
                        finally:
                            read.assert_not_called()

    def test_alert_merkle_vectors_use_display_txids_in_rpc_order(self):
        ids = [{"txid": hid(i), "hash": hid(i+100)} for i in (1, 2, 3)]
        roots = [hid(0), hid(1), "ff8f6e7ac7aa0bfc98af8be24ed6ae1ab243f64aaa065366b2215e697fddb64b",
                 "e9ffb584c62449f157c8be88257bd1eebb2d8ef824f5c86b43c4f8fd9e800d6a"]
        for n, root in enumerate(roots):
            self.assertEqual(journal.alert_merkle_root(ids[:n]), root)
        self.assertNotEqual(journal.alert_merkle_root(ids[::-1]), roots[-1])

    def test_unknown_network_and_mismatched_genesis_refused_before_index_reads(self):
        for genesis in (None, hid(999), MAINNET):
            with self.subTest(genesis=genesis), patch.object(journal, "load") as read:
                with self.assertRaisesRegex(ValueError, "genesis"):
                    journal.plan(block(0, "settle"), None, genesis)
                read.assert_not_called()

    def test_two_argument_ingest_checks_core_genesis_before_planning(self):
        for genesis in (None, hid(999)):
            canonical = Mock(return_value=genesis)
            with self.subTest(genesis=genesis), patch.object(journal, "collection") as db:
                with self.assertRaisesRegex(ValueError, "unknown BTCV Core genesis"):
                    journal.ingest_block(block(1, "settle"), canonical)
                canonical.assert_called_once_with(0)
                db.assert_not_called()

    def test_daemon_reuses_genesis_only_within_one_catchup(self):
        import daemon
        tip = {"_id": hid(101), "height": 1}
        canonical = {0: REGTEST, 1: tip["_id"]}.__getitem__
        with patch.object(daemon, "get_block_hash", side_effect=canonical) as lookup, \
                patch.object(daemon, "get_block_count", return_value=3), \
                patch.object(daemon, "get_block", side_effect=lambda h: block(h, "settle")), \
                patch.object(journal, "apply_pending", return_value=False), \
                patch.object(journal, "checkpoint", return_value=tip), \
                patch.object(journal, "ingest_block") as ingest, patch.object(daemon, "update_analyzer"):
            for poll in (1, 2):
                daemon.update_with_journal()
                self.assertEqual(lookup.call_args_list.count(call(0)), poll)
                self.assertEqual(ingest.call_count, poll * 2)
            self.assertTrue(all(kwargs == {"genesis_hash": REGTEST} and args[1] is lookup
                                for args, kwargs in ingest.call_args_list))

    def test_daemon_rejects_unknown_genesis_before_pending_replay_can_proceed(self):
        import daemon
        with patch.object(daemon, "get_block_hash", return_value=hid(999)) as lookup, \
                patch.object(journal, "apply_pending", side_effect=lambda check_hash: check_hash(222)), \
                patch.object(journal, "ingest_block") as ingest:
            with self.assertRaisesRegex(ValueError, "unknown BTCV Core genesis"):
                daemon.update_with_journal()
            lookup.assert_called_once_with(0)
            ingest.assert_not_called()

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
        raw["alertmerkleroot"] = hid(31)
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
