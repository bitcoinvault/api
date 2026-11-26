# Bitcoin Vault Api

A simple public JSON API written in Python and Flask. Includes a proposed production deployment system using Docker, NGINX, uWSGI and MongoDB. Deployed under http://api.bitcoinvault.global

The API consists of 4 parts closed in separate Docker containers: the NGINX server redirecting queries to the Flask application, the Flask application executing incoming queries, BTCV node and a database that stores blockchain statistics along with a daemon updating the data.

# NGINX and Flask app

Both components work synergistically to handle requests coming from outside. NGINX is a kind of avant-garde when it comes to handling incoming queries. Sending the request to the API takes place through the handling of the http protocol (port 8080) to the appropriate address on which the service was hosted. Then, using the uWSGI protocol, the query is forwarded to the Flask application running on port 5000. By connecting to the database, the application handles user queries and returns the required results.

At the moment, there are 6 queries that the user can request::

```python
/miningdifficulty
```

The average mining difficulty query for x consecutive blocks in the last y blocks, where x and y are counted based on the given period. The request has one parameter `period`, which can take three values: `week`, `month`, `all` (every other value than `week` and `month`).

`week` (default): average mining difficulty is calculated every 6 consecutive blocks for the last 1008 blocks of blockchain,

`month`: average mining difficulty is calculated every 144 consecutive blocks for the last 4320 blocks of blockchain,

`all`: average mining difficulty is calculated every 1008 consecuitve blocks for all blocks of blockchain.
    
```python
/transactionvolume
```

The average transaction volume query for x consecutive blocks in the last y blocks, where x and y are counted based on the given period. The request has one parameter `period`, which can take three values: `week`, `month`, `all` (every other value than `week` and `month`).

`week` (default): average transaction volume is calculated every 6 consecutive blocks for the last 1008 blocks of blockchain,

`month`: average transaction volume is calculated every 144 consecutive blocks for the last 4320 blocks of blockchain,

`all`: average transaction volume is calculated every 1008 consecuitve blocks for all blocks of blockchain.
    
```python
/nonemptywalletsnumber
```

The query returns the number of current addresses where the BTCV coin balance is greater than 0.

```python
/piechartdata
```

The query returns the address distribution for coinbase transactions from the last 100 blocks.

```python
/richestwallets
```

The query returns a list of 20 addresses with the most BTCV coins at the current moment, along with their balance.

```python
/status
```

The query returns the current blockchain height, network hashrate and the number of all coins currently in circulation.

# Bitcoin Vault node

This component gets the current version of the BTCV node from the Github repository: `https://github.com/bitcoinvault/bitcoinvault`.
The task of this component is to synchronize the BTCV blockchain so that the data on which the analysis is performed is up-to-date.

# MongoDB

This component runs the Mongo database in which blockchain statistics necessary to execute queries for the API are stored. The component actually consists of two parts: the Mongo database and the daemon, which updates this database with data by processing the blockchain. Daemon queries the BTCV node about the blockchain status every 5 seconds and updates the statistics if the database status does not match the blockchain status.

The differences between the state of the base and the state of the blockchain may be due to two reasons: the database does not have statistics from all blocks available in the blockchain (the number of processed blocks for statistics is smaller than the actual number of blocks in the chain) or the reorg occured in blockchain. Both situations are properly handled: the first - by processing the missing blocks and updating the database state, the second - by restoring the last state of the database before the reorg occurence and updating the database from there to the current state of the blockchain. 

Restoring the database is possible thanks to periodic database backups, made every full thousandth block. The database backup files are saved in `/var/backups/mongodb`. For creating and restoring database daemon uses `mongodump` and `mongorestore` commands provided by MongoDB.

The daemon "talks" to the BTCV node using the RPC methods provided by the node.

### Restart-safe forward indexing (opt-in)

`DAEMON_RECOVERY_MODE=journal` changes only the index writer. The default is
`legacy`; releasing the code does not enable the new mode. On standalone MongoDB,
the journal records the previous and intended UTXO and address documents before
changing them. The block document is the last business write and acts as the
committed checkpoint. The writer finishes an interrupted block on startup, even
when Core's height has not advanced. The pending record is then removed.

Enable only with a verified starting index, journaled MongoDB storage and
**one externally fenced writer**. Stop the previous writer before starting the
new one; this protocol is not a lease or an automatic takeover mechanism.
Recovery does not reconstruct previously missed balances. Before upgrading
from an earlier journal implementation, finish its pending block on the old
version and verify the checkpoint, or rebuild into a separate database. New
validation rules do not retroactively check previously recorded edits.
API queries are not transactionally isolated from an in-progress block. This mode handles ordinary
single-address outputs and explicit OP_RETURN data outputs only. Use verbose
canonical Core blocks containing both `tx` and `atx`. Before vault activation,
a vault type label on a regular transaction does not imply settlement. The
writer checks the regular transaction count and ordered IDs against `nTx` and
`merkleroot` at every height. It identifies the network by its Core genesis
and, from activation, also checks the alert IDs against `alertmerkleroot`.
For each alert, recovery or instant transaction it asks the same trusted
Core node to decode the supplied hex (`decoderawtransaction`) and checks the IDs, type,
input outpoints, output amounts, scripts and recipients before journaling.
The RPC account needs access to this read-only method. Blocks without these
transaction types require no extra RPC. An unavailable decoder or inconsistent
response stops indexing; the existing checkpoint is not advanced.
Apply regular transactions first. Core stores an early `atx` but does not reserve its inputs at or below
absolute block height 144 (not 144 blocks from vault activation). Above that
initialization window an alert in `atx` reserves its original inputs but does not credit a
recipient. The address balance measures settled ownership, not ordinary
spendability. A confirmed `TX_ALERT` or a `TX_RECOVERY` in `tx` consumes the
corresponding reserved inputs and credits only the actual outputs. Core, not
the indexer or the RPC transaction's current status, determines the canonical
inclusion height and consensus delay. A fresh
journal index checkpoints genesis without crediting its unspendable coinbase;
this does not repair an existing index. Missing or incomplete reservations,
unsupported or missing transaction type, unknown input, unsupported output, conflicting stored
state or journal at the BSON size limit is a stop, not a reason to skip a block.
The writer rechecks the block hash with Core before publishing the checkpoint
and before retiring the journal. If a reorganization is detected after a
partial write, stop serving that index and recover from a verified snapshot or
rebuild it. The journal is not a reorg undo log.

Before reverting, stop the writer and establish that `daemon_pending_block`
has no pending record and that the checkpoint and indexed data are consistent.
Older binaries do not know about the journal. Do not discard the pending
record to unblock a restart; retain the database for recovery or use a verified
consistent backup. A clean block journal does not mean there are no outstanding
vault alerts: reservations persist on UTXOs between blocks. This version checks
for both conditions once at legacy daemon startup, before processing blocks;
an untouched legacy database has no journal collection and needs no UTXO scan.
The daemon must be able to list collections on the selected database and read
UTXO index metadata on a previously journaled database. A denied catalog read
is an error, not evidence of an unused journal.
This distinction requires the complete database, including empty collections:
never drop the journal collection or switch writers on a partial restore.
A fresh journal replay prepares the sparse reservation index at genesis. Before
switching a populated index used by an earlier journal build back to legacy,
stop all writers and verify the backup. If the reservation index is missing,
prepare it offline on the intended UTXO collection (`pending_alert: 1`, sparse,
name `legacy_pending_alert_sparse_v1`); the one-time build can scan existing
data. Restart only after the compatibility check accepts the prepared database.
The check does not fence another writer or protect a downgrade to an older binary. Do not reuse a
legacy checkpoint with missing reservations, or point an older writer at an
index containing them. Use a fresh replay or a verified compatible snapshot.
Measure throughput and disk I/O on a representative index
before enabling this mode on a serving database.

With a locally built API image and a locally available MongoDB image, run the
restart regression without connecting to any existing database:

```sh
python tests/run_daemon_recovery.py worktree --runtime-image bitcoin-vault-api-ci --mongo-image mongo:6.0.3
```
RPC server parameters are provided in `rpc.py` file:

```python
url = "http://user:password@bitcoind:8332"
```
