import flask
from flask import jsonify, request
import threading
import time
from blockchain_analyzer import BlockchainAnalyzer


analyzer = BlockchainAnalyzer()
analyzer.update_stats()


app = flask.Flask(__name__)
app.config["DEBUG"] = False


@app.route('/richestwallets', methods=['GET'])
def richest_wallets():
    analyzer.update_stats()
    addresses_dict = analyzer.get_richest_wallets()
    return jsonify(addresses_dict)


@app.route('/status', methods=['GET'])
def status():
    analyzer.update_stats()
    status_dict = analyzer.get_status()
    return jsonify(status_dict)


if __name__ == '__main__':
    app.run()
