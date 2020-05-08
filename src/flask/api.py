from blockchain_analyzer import BlockchainAnalyzer
from db import db_host, drop_db, get_addresses, get_blockchain, get_utxos
from flask import Flask, jsonify, request
from flask_mongoengine import MongoEngine

app = Flask(__name__)
app.config['DEBUG'] = False
app.config['MONGODB_SETTINGS'] = {'host':db_host, 'connect':False}
db = MongoEngine()
db.init_app(app)
analyzer = BlockchainAnalyzer()

@app.before_request
def before_request():
    analyzer.set_blockchain(get_blockchain())
    analyzer.set_addresses(get_addresses())
    analyzer.set_utxos(get_utxos())

def _crop_params(interval, lower_height, upper_height):
    if interval < 1: interval = 1
    elif interval > 1008: interval = 1008
    
    if lower_height < 1: lower_height = 1
    elif lower_height > analyzer.max_block_number(): lower_height = analyzer.max_block_number()
    
    if upper_height < lower_height: upper_height = lower_height
    elif upper_height > analyzer.max_block_number(): upper_height = analyzer.max_block_number()
    
    return interval, lower_height, upper_height

def _range_params():
    interval = request.args.get('interval', default=144, type=int)
    upper_height = request.args.get('uh', default=analyzer.max_block_number(), type=int)
    lower_height = request.args.get('lh', default=upper_height - 1008, type=int)
    
    return interval, lower_height, upper_height

@app.route('/miningdifficulty', methods=['GET'])
def mining_difficulty():
    interval, lower_height, upper_height = _range_params()
    interval, lower_height, upper_height = _crop_params(interval, lower_height, upper_height)
    difficulty = analyzer.mining_difficulty(interval, lower_height, upper_height)
    return jsonify(difficulty)
    
@app.route('/transactionvolume', methods=['GET'])
def transaction_volume():
    interval, lower_height, upper_height = _range_params()
    interval, lower_height, upper_height = _crop_params(interval, lower_height, upper_height)
    volume = analyzer.transaction_volume(interval, lower_height, upper_height)
    return jsonify(volume)
    
@app.route('/nonemptywalletsnumber', methods=['GET'])
def non_empty_wallets():
    wallets_number = analyzer.non_empty_wallets_number()
    return jsonify(wallets_number)

@app.route('/piechartdata', methods=['GET'])
def piechart_data():
    interval, lower_height, upper_height = _range_params()
    if lower_height < 1: lower_height = 1
    elif lower_height > analyzer.max_block_number(): lower_height = analyzer.max_block_number()
    if upper_height < lower_height: upper_height = lower_height
    elif upper_height > analyzer.max_block_number(): upper_height = analyzer.max_block_number()
    
    piechart_data = analyzer.piechart_data(interval, lower_height, upper_height)
    return jsonify(piechart_data)

@app.route('/richestwallets', methods=['GET'])
def richest_wallets():
    wallets = analyzer.richest_wallets()
    return jsonify(wallets)

@app.route('/status', methods=['GET'])
def status():
    status = analyzer.status()
    return jsonify(status)

if __name__ == '__main__':
    app.run()
