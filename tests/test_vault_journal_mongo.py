"""Standalone stage: run this module in the isolated recovery-mongo network."""
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from test_vault_journal import block, block_id, expected, hid, inventory, journal
from db.models import Address, Block, UTXO
from pymongo import MongoClient
from pymongo.collection import Collection


CRASH = 86
MONGO = "recovery-mongo"


def require_stage():
    if os.environ.get("DAEMON_RECOVERY_TEST_MONGO") != MONGO:
        raise RuntimeError("vault integration requires the isolated recovery-mongo runner")


def child(name, branch, tip, fault):
    require_stage()
    if not re.fullmatch(r"vault_[0-9a-f]{32}", name):
        raise ValueError("not a test database")
    os.environ.update(DB_PROTOCOL="mongodb", DB_URL=MONGO, DB_PORT="27017", DB_NAME=name,
                      DB_USERNAME="", DB_PASSWORD="", DB_URI_PARAMS="?serverSelectionTimeoutMS=3000",
                      PREWARM_ALL_STATS="false", DAEMON_RECOVERY_MODE="journal")
    if fault in ("legacy-reservation", "legacy-intent", "legacy-compatible"):
        del os.environ["DAEMON_RECOVERY_MODE"]

        class FirstCorePoll(Exception):
            pass

        with patch("blockchain.rpc.get_block_count", side_effect=FirstCorePoll):
            try:
                runpy.run_module("daemon", run_name="__main__")
            except FirstCorePoll:
                if fault != "legacy-compatible":
                    raise AssertionError("legacy polled Core on an incompatible database")
                return 0
            except ValueError as exc:
                prefix = {"legacy-reservation": "outstanding vault reservation:", "legacy-intent": "pending journal:"}
                if fault not in prefix or not str(exc).startswith(prefix[fault]):
                    raise
                return 84
        raise AssertionError("legacy daemon exited before the first poll")
    import daemon

    def fetch(h):
        raw = block(h, branch)
        if fault == "missing-atx" and h == 3:
            del raw["atx"]
        if fault == "incomplete-atx" and h == tip:
            raw["atx"] = []
        if fault == "empty-vout" and h == tip:
            (raw["atx"] or raw["tx"])[-1]["vout"] = []
        return raw

    bulk_write, insert_one = Collection.bulk_write, Collection.insert_one
    target = {"partial-lock": UTXO._get_collection_name(), "partial-utxo": UTXO._get_collection_name(),
              "partial-address": Address._get_collection_name()}.get(fault)

    def bulk(coll, ops, *args, **kwargs):
        if coll.name == target:
            if len(ops) < 2:
                raise AssertionError("partial-write control needs multiple operations")
            if fault == "partial-lock":
                # Stop at the first durable reservation, independent of bulk ordering.
                for op in ops:
                    bulk_write(coll, [op], *args, **kwargs)
                    if coll.count_documents({"pending_alert.txid": hid(20)}) == 1:
                        os._exit(CRASH)
                raise AssertionError("no partial reservation was observed")
            bulk_write(coll, ops[:1], *args, **kwargs)
            os._exit(CRASH)
        return bulk_write(coll, ops, *args, **kwargs)

    def insert(coll, doc, *args, **kwargs):
        result = insert_one(coll, doc, *args, **kwargs)
        if fault == "checkpoint-ack" and coll.name == Block._get_collection_name():
            os._exit(CRASH)
        return result

    daemon.get_block_count = lambda: tip
    daemon.get_block = fetch
    daemon.get_block_hash = lambda h: block(h, branch)["hash"]
    daemon.try_to_connect()
    daemon.update_analyzer()
    # This six-height fixture starts in initialized mode; the real 144/145 boundary is tested separately.
    with patch.object(Collection, "bulk_write", bulk), patch.object(Collection, "insert_one", insert), \
            patch.object(journal, "ALERT_INIT_WINDOW", 0):
        try:
            daemon.update_blockchain()
        except ValueError as exc:
            expected = {"missing-atx": "invalid or missing alert transaction list",
                        "incomplete-atx": "missing or inconsistent alert merkle commitment",
                        "empty-vout": "invalid transaction inputs or outputs"}
            if str(exc) != expected.get(fault):
                raise
            return 83
    return 0


class VaultMongoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_stage()
        cls.client = MongoClient("mongodb://recovery-mongo:27017", serverSelectionTimeoutMS=12000)
        cls.addClassCleanup(cls.client.close)
        hello = cls.client.admin.command("hello")
        if hello.get("setName") or hello.get("msg") == "isdbgrid":
            raise AssertionError("not a standalone MongoDB")

    def setUp(self):
        self.db = self.client["vault_" + uuid.uuid4().hex]
        self.addCleanup(self.client.drop_database, self.db.name)

    def run_poll(self, branch, tip, fault="none", rc=0):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "child",
                                 self.db.name, branch, str(tip), fault], timeout=30,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, rc, result.stdout[-4000:].decode(errors="replace"))

    def docs(self, model):
        return list(self.db[model._get_collection_name()].find({}).sort("_id", 1))

    def state(self):
        return inventory(self.docs(UTXO), self.docs(Address))

    def assert_chain(self, tip):
        self.assertEqual(sorted((b["height"], b["_id"]) for b in self.docs(Block)),
                         [(h, block_id(h)) for h in range(tip + 1)])

    def assert_pending(self, tip):
        intents = list(self.db[journal.PENDING].find({}))
        self.assertEqual([(i["_id"], i["body"]["block"]["height"], i["body"]["block"]["_id"])
                          for i in intents], [(journal.KEY, tip, block_id(tip))])
        self.assertEqual(intents[0]["body"]["schema"], 1 if tip == 0 else 2)

    def assert_clean(self, tip, branch):
        self.assert_chain(tip)
        self.assertEqual(self.state(), expected(tip, branch))
        self.assertEqual(self.db[journal.PENDING].count_documents({}), 0)
        for doc in self.docs(UTXO):
            typed = UTXO._from_son(doc)
            typed.validate()
            self.assertEqual(typed.to_mongo().get("pending_alert"), doc.get("pending_alert"))

    def assert_idempotent(self, branch, tip):
        self.run_poll(branch, tip)
        self.assert_clean(tip, branch)
        before = [self.docs(m) for m in (Block, UTXO, Address)]
        self.run_poll(branch, tip)
        self.assert_clean(tip, branch)
        self.assertEqual([self.docs(m) for m in (Block, UTXO, Address)], before)

    def test_genesis_ack_loss_and_missing_alert_list_never_skip_a_height(self):
        branch = "settle"
        self.run_poll(branch, 0, "checkpoint-ack", CRASH)
        self.assert_pending(0)
        self.assert_chain(0)
        self.assertEqual(self.state(), ({}, {}))
        self.assert_idempotent(branch, 0)
        self.run_poll(branch, 6, "missing-atx", 83)
        self.assert_clean(2, branch)
        self.assert_idempotent(branch, 6)

    def test_commitment_mismatch_preserves_funded_checkpoint_without_intent(self):
        self.run_poll("settle", 1)
        self.assert_clean(1, "settle")
        before = [self.docs(m) for m in (Block, UTXO, Address)]
        self.run_poll("settle", 2, "incomplete-atx", 83)
        self.assert_clean(1, "settle")
        self.assertEqual([self.docs(m) for m in (Block, UTXO, Address)], before)
        self.run_poll("settle", 2)
        self.assert_clean(2, "settle")

    def crash_sequence(self, branch, fault):
        self.run_poll(branch, 1)
        self.assert_clean(1, branch)
        self.run_poll(branch, 2, "partial-lock", CRASH)
        self.assert_chain(1)
        self.assert_pending(2)
        utxos, balances = self.state()
        unlocked, locked = expected(1, branch)[0], expected(2, branch)[0]
        self.assertEqual(sum(v[2] is not None for v in utxos.values()), 1)
        self.assertTrue(all(utxos.get(k) in (unlocked.get(k), locked.get(k))
                            for k in set(utxos) | set(unlocked) | set(locked)))
        self.assertEqual(balances, expected(1, branch)[1])
        self.assert_idempotent(branch, 2)
        self.run_poll(branch, 4)
        self.assert_clean(4, branch)

        self.run_poll(branch, 5, fault, CRASH)
        self.assert_chain(4)
        self.assert_pending(5)
        pre, post = expected(4, branch), expected(5, branch)
        mid = self.state()
        partial = 0 if fault == "partial-utxo" else 1
        self.assertEqual(mid[1 - partial], pre[1] if partial == 0 else post[0])
        old, new, seen = pre[partial], post[partial], mid[partial]
        keys = set(old) | set(new) | set(seen)
        self.assertEqual(sum(seen.get(k) != old.get(k) for k in keys), 1)
        self.assertTrue(all(seen.get(k) in (old.get(k), new.get(k)) for k in keys))
        self.assertNotEqual(seen, new)

        # Crash again during replay, this time after durable checkpoint insertion.
        self.run_poll(branch, 5, "checkpoint-ack", CRASH)
        self.assert_pending(5)
        self.assert_chain(5)
        self.assertEqual(self.state(), post)
        self.assert_idempotent(branch, 5)
        self.assert_idempotent(branch, 6)

    def test_partial_reservation_then_delayed_settlement(self):
        self.crash_sequence("settle", "partial-address")

    def test_partial_reservation_then_multi_alert_recovery(self):
        self.crash_sequence("recover", "partial-utxo")

    def test_legacy_start_refuses_pending_intent_or_committed_reservation(self):
        self.run_poll("settle", 0, "legacy-compatible")
        self.run_poll("settle", 1)
        self.run_poll("settle", 2, "checkpoint-ack", CRASH)
        self.assert_pending(2)
        self.run_poll("settle", 2, "legacy-intent", 84)
        self.assert_pending(2)
        self.run_poll("settle", 2)
        self.assert_clean(2, "settle")
        self.run_poll("settle", 2, "legacy-reservation", 84)
        self.assert_clean(2, "settle")
        self.run_poll("settle", 5)
        self.assert_clean(5, "settle")
        self.run_poll("settle", 5, "legacy-compatible")

    def test_empty_outputs_never_publish_partial_vault_data(self):
        for branch in ("settle", "recover"):
            with self.subTest(branch=branch):
                self.db = self.client["vault_" + uuid.uuid4().hex]
                self.addCleanup(self.client.drop_database, self.db.name)
                for h in (2, 5):
                    self.run_poll(branch, h-1)
                    before = [self.docs(m) for m in (Block, UTXO, Address)]
                    self.run_poll(branch, h, "empty-vout", 83)
                    self.assertEqual([self.docs(m) for m in (Block, UTXO, Address)], before)
                    self.assert_clean(h-1, branch)


def load_tests(loader, tests, pattern):
    # The networkless CI discovery phase cannot perform a Mongo integration.
    # Explicit invocation always runs the suite and fails if the staging alias is absent.
    if pattern is not None and os.environ.get("DAEMON_RECOVERY_TEST_MONGO") is None:
        return unittest.TestSuite()
    return tests


if __name__ == "__main__":
    if sys.argv[1:2] == ["child"]:
        sys.exit(child(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]))
    require_stage()
    unittest.main()
