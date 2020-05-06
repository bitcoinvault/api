from db import create_address, db_host, db_name, get_highest_block_number_in_db, save_address, save_block
from mongoengine import connect, disconnect
from rpc import get_block, get_block_count
import argparse
import logging
import sys, os, time, atexit
from _signal import SIGTERM

class Daemon:
    def __init__(self, pidfile, interval, capacity):
        self.pidfile = pidfile
        self.interval = interval
        self.capacity = capacity
        self.pids = []
        
    def _stall_if_running_by_docker(self, pid):
        if self.capacity == 'docker':
            while True:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
        sys.exit(0)
        
    def daemonize(self):
        try:
            pid = os.fork()
            if pid > 0:
                self._stall_if_running_by_docker(pid)
        except OSError as err:
            sys.stderr.write('fork #1 failed: {0})\n'.format(err)) 
            sys.exit(1)
            
        os.chdir('/')
        os.setsid()
        os.umask(0)
        
        try:
            pid = os.fork()
            if pid > 0:
                self._stall_if_running_by_docker(pid)
        except OSError as err:
            sys.stderr.write('fork #2 failed: {0}\n'.format(err))
            sys.exit(1)
            
        sys.stdout.flush()
        sys.stderr.flush()
        
        atexit.register(self.delpid)
        pid = str(os.getpid())
        with open(self.pidfile, 'w+') as f:
            f.write(pid + '\n')

    def delpid(self):
        os.remove(self.pidfile)
        
    def getpid(self):
        try:
            with open(self.pidfile, 'r') as pf:
                return int(pf.read().strip())
        except IOError:
            return None
        
    def start(self):
        pid = self.getpid()
            
        if pid:
            message = 'pidfile {0} already exist; daemon already running?\n'
            sys.stderr.write(message.format(self.pidfile))
            sys.exit(1)
            
        self.daemonize()
        self.run()
        
    def stop(self):
        disconnect()
        pid = self.getpid()
        
        if not pid:
            message = 'pidfile {0} does not exist; daemon not running?\n'
            sys.stderr.write(message.format(self.pidfile))
            return
        
        try:
            while 1:
                os.kill(pid, SIGTERM)
                time.sleep(0.1)
        except OSError as err:
            e = str(err.args)
            if e.find('No such process') > 0:
                if os.path.exists(self.pidfile):
                    os.remove(self.pidfile)
            else:
                print(e)
                sys.exit(1)
                
    def restart(self):
        self.stop()
        self.start()
        
    def run(self):
        connect(db_name, host=db_host)
        while True:
            update_blockchain()
            sys.stdout.flush()
            sys.stderr.flush()
            time.sleep(self.interval)
            
def parse_action(daemon, arg):
    if arg == 'start':
        daemon.start()
    elif arg == 'stop':
        daemon.stop()
    elif arg == 'restart':
        daemon.restart()
    else:
        print('Unknown action')
        sys.exit(2)
        
def update_addresses(utxo):
    addresses = {}
    for db_utxo in utxo.values():
        txid = db_utxo.id[:-1]
        addr = db_utxo.address
        amount = db_utxo.value
        if addr not in addresses:
            addresses[addr] = create_address(addr, amount)
        addresses[addr].balance += amount
        
        if txid not in addresses[addr].txs:
            addresses[addr].txs.append(txid)
            
    for address in addresses.values():
        save_address(address)
        
def update_blockchain():
    start_block_number = get_highest_block_number_in_db() + 1
    end_block_number = get_block_count() + 1
    new_utxos = {}
    
    for idx in range(start_block_number, end_block_number):
        block = get_block(idx)
        utxos = save_block(block)
        new_utxos = {**new_utxos, **utxos}
    update_addresses(new_utxos)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage {0} -a start|stop|restart'.format(sys.argv[0]))
        sys.exit(2)
        
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--capacity', default='docker')
    parser.add_argument('-i', '--interval', default=5)
    parser.add_argument('-p', '--pid-file', default='/tmp/blockchain-daemon.pid')
    parser.add_argument('-a', '--action', required=True)
    args = parser.parse_args()
    
    daemon = Daemon(args.pid_file, args.interval, args.capacity)
    parse_action(daemon, args.action)