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

period_block_map = {
    'hour' : 6,
    'day' : 144,
    'week' : 1008,
    'month' : 4320
}

@app.before_request
def before_request():
    analyzer.set_blockchain(get_blockchain())
    analyzer.set_addresses(get_addresses())
    analyzer.set_utxos(get_utxos())

def _crop_params(interval, lower_height, upper_height):
    if interval < 1: interval = 1
    elif interval > 4320: interval = 4320
    
    if lower_height < 1: lower_height = 1
    elif lower_height > analyzer.max_block_number(): lower_height = analyzer.max_block_number()
    
    if upper_height < lower_height: upper_height = lower_height
    elif upper_height > analyzer.max_block_number(): upper_height = analyzer.max_block_number()
    
    return interval, lower_height, upper_height

def period_to_block_range_and_interval(period):
    upper_height = analyzer.max_block_number()
    
    if period == 'week': return _crop_params(period_block_map['hour'], upper_height - period_block_map['week'], upper_height)
    if period == 'month' : return _crop_params(period_block_map['day'], upper_height - period_block_map['month'], upper_height)
    
    return _crop_params(period_block_map['week'], 1, upper_height)

@app.route('/miningdifficulty', methods=['GET'])
def mining_difficulty():
    period = request.args.get('period', default='week')
    interval, lower_height, upper_height = period_to_block_range_and_interval(period)
    difficulty = analyzer.mining_difficulty(interval, lower_height, upper_height)
    return jsonify(difficulty)
    
@app.route('/transactionvolume', methods=['GET'])
def transaction_volume():
    period = request.args.get('period', default='week')
    interval, lower_height, upper_height = period_to_block_range_and_interval(period)
    volume = analyzer.transaction_volume(interval, lower_height, upper_height)
    return jsonify(volume)
    
@app.route('/nonemptywalletsnumber', methods=['GET'])
def non_empty_wallets():
    wallets_number = analyzer.non_empty_wallets_number()
    return jsonify(wallets_number)

@app.route('/piechartdata', methods=['GET'])
def piechart_data():
    piechart_data = analyzer.piechart_data(100, -1, -1)
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
