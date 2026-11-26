"""Bounded startup checks; the Mongo/explain case runs only in the isolated fixture."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bson import BSON
from pymongo import MongoClient
from db import block_recovery as r


class LegacyGuardTest(unittest.TestCase):
    def setUp(self):
        self.pending, self.utxos, self.blocks = Mock(), Mock(), Mock()
        self.pending.find_one.return_value = None
        self.catalog = self.pending.database.list_collection_names
        self.catalog.return_value = [r.PENDING]
        self.indexes = {"_id_": {"key": [("_id", 1)]}}
        self.spec = {"key": [("pending_alert", 1)], "sparse": True}
        self.utxos.index_information.side_effect = lambda: self.indexes.copy()
        self.utxos.find_one.return_value = None
        collections = {r.PENDING: self.pending, r.UTXO._get_collection_name(): self.utxos,
                       r.Block._get_collection_name(): self.blocks}
        mocked = patch.object(r, "collection", side_effect=collections.__getitem__)
        self.collection = mocked.start()
        self.addCleanup(mocked.stop)

    def test_no_journal_namespace_never_opens_utxos(self):
        self.catalog.return_value = []
        r.require_legacy_compatible()
        self.collection.assert_called_once_with(r.PENDING)
        self.pending.find_one.assert_called_once_with({"_id": r.KEY}, {"_id": 1})
        self.catalog.assert_called_once_with(filter={"name": r.PENDING})
        self.catalog.side_effect = RuntimeError("catalog unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            r.require_legacy_compatible()

    def test_even_malformed_active_intent_stops_before_catalog(self):
        self.pending.find_one.return_value = {"_id": r.KEY}
        with self.assertRaisesRegex(ValueError, "pending journal"):
            r.require_legacy_compatible()
        self.catalog.assert_not_called()
        self.utxos.index_information.assert_not_called()

    def test_used_journal_without_index_only_probes_one_primary_key(self):
        for primary, row in [(False, None), (True, None), (True, {"_id": "coin"})]:
            with self.subTest(primary=primary, row=row):
                self.utxos.index_information.side_effect = lambda: self.indexes if primary else {}
                self.utxos.find_one.return_value = row
                if row:
                    with self.assertRaisesRegex(ValueError, "prepare the reservation index offline"):
                        r.require_legacy_compatible()
                else:
                    r.require_legacy_compatible()
                self.utxos.find_one.assert_called_with({}, {"_id": 1}, **({"hint": "_id_"} if primary else {}))
                self.utxos.create_index.assert_not_called()

    def test_wrong_named_index_never_allows_or_rebuilds(self):
        for override in ({"sparse": False}, {"key": [("pending_alert.txid", 1)]},
                         {"partialFilterExpression": {"pending_alert.txid": {"$exists": True}}},
                         {"hidden": True}, {"buildUUID": "building"}):
            with self.subTest(override=override):
                self.indexes[r.RESERVATION_INDEX] = dict(self.spec, **override)
                for check in (r.require_legacy_compatible, r.prepare_legacy_reservation_index):
                    with self.assertRaisesRegex(ValueError, "definition"):
                        check()
        self.utxos.create_index.assert_not_called()
        self.utxos.find_one.assert_not_called()

    def test_hint_failure_is_not_permission_to_fall_back(self):
        self.indexes[r.RESERVATION_INDEX] = self.spec
        self.utxos.find_one.side_effect = RuntimeError("index unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            r.require_legacy_compatible()
        self.utxos.find_one.assert_called_once_with({"pending_alert": {"$exists": True}},
                                                   {"_id": 1}, hint=r.RESERVATION_INDEX)

    def test_lost_genesis_build_ack_leaves_intent_for_idempotent_replay(self):
        body = {"schema": 1, "previous": None, "block": {"_id": "a"*64, "height": 0},
                "utxos": [], "addresses": []}
        intent = {"_id": r.KEY, "body": body, "checksum": hashlib.sha256(BSON.encode(body)).hexdigest()}
        self.pending.find_one.return_value = intent
        self.pending.delete_one.return_value.deleted_count = 1
        self.blocks.find_one.return_value = None

        def ambiguous_build(*args, **kwargs):
            self.indexes[r.RESERVATION_INDEX] = self.spec
            raise RuntimeError("lost build acknowledgement")

        self.utxos.create_index.side_effect = ambiguous_build
        with patch.object(r, "checkpoint", return_value=None), patch.object(r, "apply_edits") as edits:
            with self.assertRaisesRegex(RuntimeError, "lost build"):
                r.apply_pending(lambda h: "a"*64)
            edits.assert_not_called()
            self.blocks.insert_one.assert_not_called()
            self.pending.delete_one.assert_not_called()
            with self.assertRaisesRegex(ValueError, "pending journal"):
                r.require_legacy_compatible()
            self.assertTrue(r.apply_pending(lambda h: "a"*64))
        self.blocks.insert_one.assert_called_once_with(body["block"])
        self.pending.delete_one.assert_called_once_with({"_id": r.KEY, "checksum": intent["checksum"]})
        self.utxos.create_index.assert_called_once_with([("pending_alert", 1)], name=r.RESERVATION_INDEX, sparse=True)


@unittest.skipUnless(os.environ.get("DAEMON_RECOVERY_TEST_MONGO") == "recovery-mongo",
                     "requires the dedicated standalone recovery-mongo fixture")
class LegacyGuardMongoTest(unittest.TestCase):
    def test_stock_namespace_old_journal_migration_and_query_plan(self):
        client = MongoClient("mongodb://recovery-mongo:27017", serverSelectionTimeoutMS=5000)
        self.addCleanup(client.close)
        name = "vault_" + uuid.uuid4().hex
        self.addCleanup(client.drop_database, name)
        db = client[name]
        utxos = db[r.UTXO._get_collection_name()]
        query = {"pending_alert": {"$exists": True}}
        with patch.object(r, "get_db", return_value=db):
            r.require_legacy_compatible()
            self.assertEqual(db.list_collection_names(), [])
            utxos.insert_many([{"_id": str(i), "address": "A", "value": 1.0} for i in range(1000)])
            r.require_legacy_compatible()
            self.assertEqual(db.list_collection_names(), [utxos.name])
            # Exercise the real startup and MongoEngine QuerySet setup through its first Core poll.
            entry = Path(__file__).with_name("test_vault_journal_mongo.py")
            subprocess.run([sys.executable, "-B", str(entry), "child", name, "settle", "0", "legacy-compatible"],
                           check=True, timeout=30)
            self.assertNotIn(r.PENDING, db.list_collection_names())
            self.assertNotIn(r.RESERVATION_INDEX, utxos.index_information())

            # An old writer inserted then retired its last intent. The namespace survived.
            db[r.PENDING].insert_one({"_id": r.KEY})
            with self.assertRaisesRegex(ValueError, "pending journal"):
                r.require_legacy_compatible()
            db[r.PENDING].delete_one({"_id": r.KEY})
            utxos.update_one({"_id": "0"}, {"$set": {"pending_alert": None}})
            with self.assertRaisesRegex(ValueError, "prepare the reservation index offline"):
                r.require_legacy_compatible()
            self.assertNotIn(r.RESERVATION_INDEX, utxos.index_information())
            r.prepare_legacy_reservation_index()
            r.prepare_legacy_reservation_index()
            for mark in (None, {}, [], False, {"txid": "a"*64}):
                with self.subTest(mark=mark):
                    utxos.update_one({"_id": "0"}, {"$set": {"pending_alert": mark}})
                    with self.assertRaisesRegex(ValueError, "outstanding vault reservation"):
                        r.require_legacy_compatible()
            utxos.update_one({"_id": "0"}, {"$unset": {"pending_alert": ""}})
            r.require_legacy_compatible()
            command = {"find": utxos.name, "filter": query, "projection": {"_id": 1},
                       "limit": 1, "hint": r.RESERVATION_INDEX}
            stats = db.command("explain", command, verbosity="executionStats")["executionStats"]
            self.assertEqual((stats["nReturned"], stats["totalKeysExamined"], stats["totalDocsExamined"]),
                             (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
