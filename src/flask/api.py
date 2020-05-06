from blockchain_analyzer import BlockchainAnalyzer
from db import db_host, drop_db, get_addresses, get_blockchain, get_utxos
from flask import Flask, jsonify, request
from flask_mongoengine import MongoEngine

app = Flask(__name__)
app.config["DEBUG"] = False
app.config['MONGODB_SETTINGS'] = {'host':db_host, 'connect':False}
db = MongoEngine()
db.init_app(app)
analyzer = BlockchainAnalyzer()

@app.before_request
def before_request():
    analyzer.set_blockchain(get_blockchain())
    analyzer.set_addresses(get_addresses())
    analyzer.set_utxos(get_utxos())

@app.route('/richestwallets', methods=['GET'])
def richest_wallets():
    wallets = analyzer.get_richest_wallets()
    return jsonify(wallets)

@app.route('/status', methods=['GET'])
def status():
    status = analyzer.get_status()
    return jsonify(status)

if __name__ == '__main__':
    app.run()
