from datetime import datetime
from math import floor
from models import RequestCache
import json

def get_nonempty_wallets_number_query():
    pipeline = [
        { '$match' : {'balance' : { '$gt' : 0}}
        },
        { '$group' : {
                '_id' : 'null',
                'count' : { '$sum' : 1}
            }
        }
    ]
    return pipeline

def get_highest_block_number_in_db_query():
    pipeline = [
        {   '$sort' : {'height' : -1}
        },
        {   '$limit' : 1
        },
        {   '$project': {
                'height' : 1
            }
        }
    ]
    return pipeline

def get_blocks_in_range_query(lower_height, upper_height, projection):
    pipeline = [
        { '$match' : { 'height' : {'$gte' : lower_height}}},
        { '$match' : { 'height' : {'$lte' : upper_height}}},
        { '$sort': {'height' : 1}
        },
        { '$project': projection
        }
    ]
    return pipeline

def get_last_n_blocks_query(interval, projection):
    pipeline = [
        { '$sort': {'height' : -1}
        },
        { '$limit': interval 
        },
        { '$project': projection 
        }
    ]
    return pipeline

def get_total_coin_supply_query():
    pipeline = [
        {   '$group' : {
                '_id' : 'null',
                'total_coin_supply' : { '$sum' : '$balance' }
            }
        },
        {   '$project' : {
                'total_coin_supply' : 1,
                '_id' : 0
            }
        }
    ]
    return pipeline

def get_blocks_coinbase_addresses_count_query(interval, lower_height, upper_height):
    projection = {'tx' : { '$arrayElemAt': ['$tx', 0]}}
    if interval > 0:
        pipeline = get_last_n_blocks_query(interval, projection)
    else:
        pipeline = get_blocks_in_range_query(lower_height, upper_height, projection)
    pipeline += [
        { '$unwind' : '$tx.vout' },
        { '$match' : { 'tx.vout.scriptPubKey.addresses' : {'$ne' : []}}},
        { '$project' : {
                'addresses' : '$tx.vout.scriptPubKey.addresses'
            }
        },
        { '$group' : {
                '_id' : '$addresses',
                'count' : { '$sum' : 1}
            }
        },
        { '$project' : {
                'address' : { '$arrayElemAt' : ['$_id', 0]},
                'count' : 1,
                '_id' : 0
            }
        }
    ]
    
    return pipeline

def get_transactions_average_volume_query(interval, lower_height, upper_height):
    projection = {
        'height' : 1,
        'time' : 1,
        'tx' : { 
            '$cond' : {
                'if' : {'$eq' : [{'$size' : '$tx'}, 1]},
                'then' : '$$REMOVE',
                'else' : {'$slice': ['$tx', 1, {'$subtract' : [{'$size': '$tx'}, 1]}]}
            }
        }
    }
    boundaries = [lower_height + i * interval for i in range(int((upper_height - lower_height) / interval) + 1)]
    pipeline = get_blocks_in_range_query(lower_height, upper_height, projection)
    pipeline += [
        {   '$project' : {
                'height' : 1,
                'value' : { 
                    '$sum' : {
                        '$reduce' : {
                            'input' : '$tx.vout.value',
                            'initialValue' : [],
                            'in' : { '$concatArrays' : ['$$value', '$$this']}
                        }
                    }
                }
            }
        },
        {   '$match' : { 
                'value' : { '$gt' : 0 }
            }
        },
        {   '$bucket' : {
                'groupBy': '$height',
                'boundaries': boundaries,
                'default': boundaries[-1],
                'output': {
                    'lower_height' : {'$min' : '$height'},
                    'upper_height' : {'$max' : '$height'},
                    'sum' : { '$sum' : '$value' },
                    'avg' : { '$avg' : '$value' },
                    'std' : { '$stdDevPop' : '$value' },
                    'min' : { '$min' : '$value' },
                    'max' : { '$max' : '$value' },
                    'count' : { '$sum' : 1 }
                }
            }
        }
    ]
    return pipeline
    
def get_blocks_average_difficulty_query(interval, lower_height, upper_height):
    projection = {
        'time' : 1,
        'height' : 1,
        'difficulty' : 1
    }
    boundaries = [lower_height + i * interval for i in range(int((upper_height - lower_height) / interval) + 1)]
    pipeline = get_blocks_in_range_query(lower_height, upper_height, projection)
    pipeline += [
        { '$bucket' : {
            'groupBy': '$height',
            'boundaries': boundaries,
            'default': boundaries[-1],
            'output': {
                'lower_height' : {'$min' : '$height'},
                'upper_height' : {'$max' : '$height'},
                'avg_difficulty': {'$avg': '$difficulty'}
                }
            }
        }
    ]
    return pipeline
