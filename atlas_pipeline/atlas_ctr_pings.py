import requests
import pandas as pd
import multiprocessing as mp
from collections import defaultdict
import os
import time


BUILT_IN = {1030:'ams02', 
            1031:'ams03',
            1029:'ewr01',
            1028:'fnc01',
            1017:'nue13',
            1019:'nue17',
            1027:'sin02'}


def process_pings(rtt, data):
    for res in data:
        try:
            if res['min'] > 0:
                rtt[res['prb_id']] = min(res['min'],  rtt[res['prb_id']])
        except:
            continue


def get_one_builtin(t, i):
    rtt = defaultdict(lambda: 9999)
    for step in range(3):
        t += 21600*step
        try:
            url = 'https://atlas.ripe.net/api/v2/measurements/{}/results/?format=json&start={}&stop={}'.format(i, t, t+500)
            response = requests.get(url)
            data = response.json()
           
            process_pings(rtt, data)
        except:
            continue
    return rtt


def process_builtins(t):
    output = {}
    for i in BUILT_IN:
        output[BUILT_IN[i]] = get_one_builtin(t, i)
    df = pd.DataFrame(output).reset_index().rename(columns={'index':'id'})
    df['snapshot'] = pd.to_datetime(t, unit='s')
    df.to_csv('atlas_ctr/ping_{}.csv.gz'.format(t), index=False)

today = int(time.time())

if __name__ == '__main__':
    try:
        pool = mp.Pool(10)
        res = pool.map(process_builtins, list(range(today-86400*1, today, 86400)))
    except:
        pool.terminate()
        raise Exception


dfl = sorted(os.listdir('atlas_ctr/'))


r = []
for i in dfl:
    r.append(pd.read_csv('atlas_ctr/' + i))


df = pd.concat(r)
df.to_csv('ping_past_week.csv.gz', index=False)