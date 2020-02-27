import flask
from flask import jsonify
import threading
import time
from blockchain_analyzer import BlockchainAnalyzer


def update():
    global status
    global addresses
    while True:
        analyzer.update_stats()
        with lock_status:
            status = analyzer.get_status()
        with lock_addresses:
            addresses = analyzer.get_richest_wallets()
        time.sleep(60)


lock_status = threading.Lock()
lock_addresses = threading.Lock()
analyzer = BlockchainAnalyzer()
analyzer.update_stats()
status = analyzer.get_status()
addresses = analyzer.get_richest_wallets()

t1 = threading.Thread(target=update)
t1.setDaemon(True)
t1.start()

app = flask.Flask(__name__)
app.config["DEBUG"] = False


@app.route('/richestwallets', methods=['GET'])
def richest_wallets():
    with lock_addresses:
        return jsonify(addresses)


@app.route('/status', methods=['GET'])
def status():
    with lock_status:
        return jsonify(status)

if __name__ == '__main__':
    app.run()
