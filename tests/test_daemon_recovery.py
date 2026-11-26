"""Real standalone Mongo + fresh daemon processes; use run_daemon_recovery.py."""
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


SEED = 1008
CRASH = 86
ISOLATED = os.environ.get("DAEMON_RECOVERY_TEST_MONGO") == "recovery-mongo"


def hexid(n):
    return format(n, "064x")


def block_id(h):
    return "01cead69f2b51e214e1c2cd50e3744428cae526db87b2ff8f32489ff801d0f1d" if h == 0 else hexid(h+1)


def tx(n, inputs, outputs):
    return {"txid": hexid(n), "hash": hexid(n), "type": "TX_NONVAULT",
            "vin": [{"txid": hexid(i), "vout": v} for i, v in inputs] or [{"coinbase": "00"}],
            "vout": [{"n": i, "value": v, "scriptPubKey": {"addresses": [a]}}
                     for i, (a, v) in enumerate(outputs)]}


def block(height):
    if height in (SEED, SEED + 1, SEED + 2):
        transactions = [tx(1000000 + height, [], [("fixture-A" if height == SEED else "fixture-M", 8.0)])]
    else:
        transactions = {
            0: [tx(19, [], [("genesis", 175.0)])],
            1: [tx(20, [], [("A", 8.0)])],
            2: [tx(21, [(20, 0)], [("bridge", 5.125), ("A", 2.875)]),
                tx(22, [(21, 0)], [("B", 5.125)])],
            3: [tx(23, [(22, 0)], [("C", 5.125)])]
        }[height]
    return {"hash": block_id(height), "previousblockhash": block_id(height-1) if height else hexid(0),
            "height": height, "time": 1700000000 + 600 * height, "difficulty": 4.0,
            "atx": [], "alertmerkleroot": hexid(0), "tx": transactions}


def child(name, tip, fault):
    # No externally supplied Mongo URI or credentials. Only the runner's private alias.
    if not ISOLATED or not name.startswith("acct_") or len(name) != 37:
        raise RuntimeError("requires a run-owned fixture database")
    uuid.UUID(hex=name[5:])
    os.environ.update(DB_PROTOCOL="mongodb", DB_URL="recovery-mongo", DB_PORT="27017", DB_NAME=name,
                      DB_USERNAME="", DB_PASSWORD="", DB_URI_PARAMS="?serverSelectionTimeoutMS=3000",
                      PREWARM_ALL_STATS="false")
    import daemon
    from db.models import Address, Block, UTXO
    from pymongo.collection import Collection

    hashes = []

    def canonical(height):
        if height not in (0, 1, 2, 3, SEED, SEED + 1, SEED + 2) or not 0 <= height <= tip:
            raise AssertionError("unexpected Core height: %s" % height)
        hashes.append(height)
        return hexid(9000) if fault == "changed-hash" and height == tip else block_id(height)

    def fetch(height):
        canonical(height)
        if fault == "fetch" and height == SEED + 2:
            raise IOError("fixture block fetch failed")
        raw = block(height)
        if height == tip and fault in ("TX_ALERT", "TX_RECOVERY"):
            raw["tx"][0]["type"] = fault
        return raw

    # Instrument real writes, not the journal's plan or replay functions.
    bulk_write, insert_one = Collection.bulk_write, Collection.insert_one

    def bulk(coll, requests, *args, **kwargs):
        names = {"partial-utxo": UTXO._get_collection_name(),
                 "partial-address": Address._get_collection_name()}
        if names.get(fault) == coll.name:
            if len(requests) < 2:
                raise AssertionError("fault requires a real multi-edit batch")
            bulk_write(coll, requests[:1], *args, **kwargs)
            os._exit(CRASH)  # only the first operation persisted
        return bulk_write(coll, requests, *args, **kwargs)

    def insert(coll, doc, *args, **kwargs):
        result = insert_one(coll, doc, *args, **kwargs)
        if fault == "checkpoint-ack" and coll.name == Block._get_collection_name():
            os._exit(CRASH)  # the caller never sees the acknowledged durable write
        return result

    daemon.get_block_count = lambda: tip
    daemon.get_block = fetch
    daemon.get_block_hash = canonical  # harmless on upstream, required by journal mode
    daemon.try_to_connect()
    daemon.update_analyzer()
    with patch.object(Collection, "bulk_write", bulk), patch.object(Collection, "insert_one", insert):
        try:
            daemon.update_blockchain()
        except IOError as exc:
            if fault == "fetch" and str(exc) == "fixture block fetch failed":
                return 81
            raise
        except ValueError as exc:
            if fault == "changed-hash" and "canonical" in str(exc) and tip in hashes:
                return 82
            if fault in ("TX_ALERT", "TX_RECOVERY") and "without an indexed reservation" in str(exc):
                return 83
            raise
    return 0


@unittest.skipUnless(ISOLATED, "requires the isolated Docker runner")
class RecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pymongo import MongoClient
        cls.client = MongoClient("mongodb://recovery-mongo:27017", serverSelectionTimeoutMS=15000)
        cls.addClassCleanup(cls.client.close)
        hello = cls.client.admin.command("hello")
        if hello.get("setName") or hello.get("msg") == "isdbgrid":
            raise AssertionError("requires standalone MongoDB")
        print("MongoDB", cls.client.server_info()["version"], "standalone", flush=True)

    def fresh(self, seed=False):
        name = "acct_" + uuid.uuid4().hex
        db = self.client[name]
        self.addCleanup(self.client.drop_database, name)
        if seed:
            from db.db_utils import create_address, create_block, create_utxo, set_insert_or_update_time
            from db.models import Address, Block, UTXO
            for cls, item in [(Block, create_block(block(SEED))),
                              (UTXO, create_utxo(hexid(1000000 + SEED) + "0", "fixture-A", 8)),
                              (Address, create_address("fixture-A", 8))]:
                set_insert_or_update_time(item)
                item.validate()
                db[cls._get_collection_name()].insert_one(item.to_mongo())
        return db

    def cycle(self, db, tip, fault="none", expected=0, mode="journal"):
        cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "child", db.name, str(tip), fault]
        proc = subprocess.run(cmd, env=dict(os.environ, DAEMON_RECOVERY_MODE=mode),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(proc.returncode, expected, proc.stdout[-4000:].decode(errors="replace"))

    def state(self, db):
        from db.models import Address, Block, UTXO
        # Only clock fields differ between independent executions.
        projection = {"insert_time": 0, "update_time": 0}
        return {k: list(db[m._get_collection_name()].find({}, projection).sort("_id", 1))
                for k, m in [("balances", Address), ("utxos", UTXO), ("blocks", Block)]}

    def amounts(self, state):
        return {r["_id"]: r["balance"] for r in state["balances"]}

    def test_midchunk_restart(self):
        ref, restarted = self.fresh(seed=True), self.fresh(seed=True)
        self.cycle(ref, SEED + 1)
        same_tip = self.state(ref)
        self.cycle(ref, SEED + 2)
        caught_up = self.state(ref)
        self.assertEqual(self.amounts(same_tip), {"fixture-A": 8, "fixture-M": 8})
        self.assertEqual(self.amounts(caught_up), {"fixture-A": 8, "fixture-M": 16})

        self.cycle(restarted, SEED + 2, "fetch", 81)
        interrupted = self.state(restarted)
        self.assertEqual(interrupted["blocks"], same_tip["blocks"])
        self.assertEqual(interrupted["utxos"], same_tip["utxos"])
        for tip, clean in [(SEED + 1, same_tip), (SEED + 2, caught_up)]:
            for attempt in (1, 2):
                self.cycle(restarted, tip)
                actual = self.state(restarted)
                print("mid-chunk tip=%s poll=%s clean=%s actual=%s utxos_equal=%s" % (
                    tip, attempt, self.amounts(clean), self.amounts(actual), actual["utxos"] == clean["utxos"]),
                    flush=True)
                with self.subTest(tip=tip, poll=attempt):
                    self.assertEqual(actual, clean)

    def test_genesis_checkpoints_without_crediting_coinbase(self):
        from db import block_recovery as journal
        db = self.fresh()
        self.cycle(db, 0, "checkpoint-ack", CRASH)
        pending = db[journal.PENDING].find_one({"_id": journal.KEY})
        self.assertEqual(pending["body"]["utxos"], [])
        self.assertEqual(pending["body"]["addresses"], [])
        for _ in range(2):
            self.cycle(db, 0)
            state = self.state(db)
            self.assertEqual([(b["height"], b["_id"]) for b in state["blocks"]], [(0, block_id(0))])
            self.assertEqual(state["balances"], [])
            self.assertEqual(state["utxos"], [])
            self.assertEqual(db[journal.PENDING].count_documents({}), 0)

    def test_regular_vault_transactions_leave_funded_index_untouched(self):
        from db import block_recovery as journal
        db = self.fresh()
        self.cycle(db, 1)
        before = self.state(db)
        self.assertEqual([u["_id"] for u in before["utxos"]], [hexid(20) + "0"])
        for tx_type in ("TX_ALERT", "TX_RECOVERY"):
            with self.subTest(tx_type=tx_type):
                self.cycle(db, 2, tx_type, 83)
                self.assertEqual(self.state(db), before)
                self.assertEqual(db[journal.PENDING].count_documents({}), 0)

    def test_spends_and_interrupted_writes(self):
        from db import block_recovery as journal
        ref = self.fresh()
        # Legacy credits genesis on a fresh sync; compare ordinary blocks from the same checkpoint.
        self.cycle(ref, 0)
        self.cycle(ref, 1, mode="legacy")
        self.cycle(ref, 2, mode="legacy")
        clean_at_two = self.state(ref)
        self.cycle(ref, 3, mode="legacy")
        clean_at_three = self.state(ref)
        self.assertEqual(self.amounts(clean_at_three), {"A": 2.875, "bridge": 0, "B": 0, "C": 5.125})
        self.assertEqual([(r["_id"], r["address"], r["value"]) for r in clean_at_three["utxos"]],
                         [(hexid(21) + "1", "A", 2.875), (hexid(23) + "0", "C", 5.125)])
        for fault in ("partial-utxo", "partial-address", "checkpoint-ack"):
            with self.subTest(fault=fault):
                db = self.fresh()
                self.cycle(db, 1)
                funded = self.state(db)
                self.cycle(db, 2, fault, CRASH)
                mid = self.state(db)
                pending = list(db[journal.PENDING].find({}))
                self.assertEqual(len(pending), 1)
                self.assertEqual(mid["blocks"], clean_at_two["blocks"] if fault == "checkpoint-ack"
                                 else funded["blocks"])
                if fault == "partial-utxo":
                    self.assertNotIn(mid["utxos"], (funded["utxos"], clean_at_two["utxos"]))
                    self.assertEqual(mid["balances"], funded["balances"])
                if fault == "partial-address":
                    self.assertEqual(mid["utxos"], clean_at_two["utxos"])
                    self.assertNotIn(mid["balances"], (funded["balances"], clean_at_two["balances"]))
                if fault == "checkpoint-ack":
                    self.assertEqual(mid, clean_at_two)
                # Reject an orphaned pending block without repairing/publishing its edits.
                self.cycle(db, 2, "changed-hash", 82)
                self.assertEqual(self.state(db), mid)
                self.assertEqual(list(db[journal.PENDING].find({})), pending)
                for tip, clean in [(2, clean_at_two), (2, clean_at_two), (3, clean_at_three), (3, clean_at_three)]:
                    self.cycle(db, tip)
                    self.assertEqual(self.state(db), clean)
                    self.assertEqual(db[journal.PENDING].count_documents({}), 0)
                self.cycle(db, 3, "changed-hash", 82)
                self.assertEqual(self.state(db), clean_at_three)
                print("Recovered %s: checkpoints 2 and 3 match clean legacy; both polled twice" % fault,
                      flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["child"]:
        sys.exit(child(sys.argv[2], int(sys.argv[3]), sys.argv[4]))
    unittest.main()
