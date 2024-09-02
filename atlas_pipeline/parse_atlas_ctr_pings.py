#!/usr/bin/env python
# coding: utf-8

import pandas as pd
import requests
import json
import urllib
import bz2
import numpy as np

pings=pd.read_csv('ping_past_week.csv.gz')

pings['snapshot']=pd.to_datetime(pings['snapshot'])


BUILT_IN = {1030:'ams02', 
            1031:'ams03',
            1029:'ewr01',
            1028:'fnc01',
            1017:'nue13',
            1019:'nue17',
            1027:'sin02'}



coors={'ewr':(40.73583613862769, -74.17381565328128), #+- 5 miles in any to distance of center
'fnc':(37.54870719189033, -121.98563153269905), #+- 6 miles in any to distance of center
'ams':(52.37375254923416, 4.895821668845044), #+- 5 miles in any to distance of center
'nue':(49.504810992423046, 11.026117090913822), #+- 5 miles in any to distance of center
'sin':(1.278648211254001, 103.85471512583199)} #+- 15.87 miles in any to distance of center



probes = urllib.request.urlopen('https://ftp.ripe.net/ripe/atlas/probes/archive/meta-latest')
probes = json.load(bz2.open(probes))
probedf = pd.DataFrame(probes['objects'])
probedf = probedf[probedf.status == 1].dropna(subset=['address_v4'])
probedf = probedf[['id', 'country_code', 'latitude', 'longitude']]


pings=pings.merge(probedf, on=['id'])

pings['ams02_latlong'] = [coors['ams']] * len(pings)
pings['ams03_latlong'] = [coors['ams']] * len(pings)
pings['ewr01_latlong'] = [coors['ewr']] * len(pings)

pings['nue13_latlong'] = [coors['nue']] * len(pings)
pings['nue17_latlong'] = [coors['nue']] * len(pings)

pings['fnc01_latlong'] = [coors['fnc']] * len(pings)

pings['sin02_latlong'] = [coors['sin']] * len(pings)




def calc_dist_vectorized(lat1, lon1, lat2, lon2):
    # Convert latitude and longitude from degrees to radians
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    
    # Earth radius in miles
    R = 3956
    return R * c



def calc_lprtt(x, err):
    if x - err > 0:
        x -= err
    return round(x/62.094, 3)



pings['lat'] = pings['latitude']
pings['long'] = pings['longitude']
pings['ams02_lat'] = pings['ams02_latlong'].apply(lambda x: x[0])
pings['ams02_long'] = pings['ams02_latlong'].apply(lambda x: x[1])
pings['ams03_lat'] = pings['ams03_latlong'].apply(lambda x: x[0])
pings['ams03_long'] = pings['ams03_latlong'].apply(lambda x: x[1])
pings['nue13_lat'] = pings['nue13_latlong'].apply(lambda x: x[0])
pings['nue13_long'] = pings['nue13_latlong'].apply(lambda x: x[1])
pings['nue17_lat'] = pings['nue17_latlong'].apply(lambda x: x[0])
pings['nue17_long'] = pings['nue17_latlong'].apply(lambda x: x[1])
pings['ewr01_lat'] = pings['ewr01_latlong'].apply(lambda x: x[0])
pings['ewr01_long'] = pings['ewr01_latlong'].apply(lambda x: x[1])
pings['fnc01_lat'] = pings['fnc01_latlong'].apply(lambda x: x[0])
pings['fnc01_long'] = pings['fnc01_latlong'].apply(lambda x: x[1])
pings['sin02_lat'] = pings['sin02_latlong'].apply(lambda x: x[0])
pings['sin02_long'] = pings['sin02_latlong'].apply(lambda x: x[1])


pings['dist_ams02'] = calc_dist_vectorized(pings['ams02_lat'], pings['ams02_long'], pings['lat'], pings['long'])
pings['dist_ams03'] = calc_dist_vectorized(pings['ams03_lat'], pings['ams03_long'], pings['lat'], pings['long'])
pings['dist_nue13'] = calc_dist_vectorized(pings['nue13_lat'], pings['nue13_long'], pings['lat'], pings['long'])
pings['dist_nue17'] = calc_dist_vectorized(pings['nue17_lat'], pings['nue17_long'], pings['lat'], pings['long'])
pings['dist_sin02'] = calc_dist_vectorized(pings['sin02_lat'], pings['sin02_long'], pings['lat'], pings['long'])
pings['dist_ewr01'] = calc_dist_vectorized(pings['ewr01_lat'], pings['ewr01_long'], pings['lat'], pings['long'])
pings['dist_fnc01'] = calc_dist_vectorized(pings['fnc01_lat'], pings['fnc01_long'], pings['lat'], pings['long'])

pings['lowest_possible_rtt_ams02'] = pings['dist_ams02'].apply(lambda x: calc_lprtt(x, 5))
pings['lowest_possible_rtt_ams03'] = pings['dist_ams03'].apply(lambda x: calc_lprtt(x, 5))
pings['lowest_possible_rtt_nue13'] = pings['dist_nue13'].apply(lambda x: calc_lprtt(x, 5))
pings['lowest_possible_rtt_nue17'] = pings['dist_nue17'].apply(lambda x: calc_lprtt(x, 5))
pings['lowest_possible_rtt_sin02'] = pings['dist_sin02'].apply(lambda x: calc_lprtt(x, 5))
pings['lowest_possible_rtt_ewr01'] = pings['dist_ewr01'].apply(lambda x: calc_lprtt(x, 5))
pings['lowest_possible_rtt_fnc01'] = pings['dist_fnc01'].apply(lambda x: calc_lprtt(x, 5))

pings['ams02_violated']=pings['ams02']<=pings['lowest_possible_rtt_ams02']
pings['ams03_violated']=pings['ams03']<=pings['lowest_possible_rtt_ams03']
pings['sin02_violated']=pings['sin02']<=pings['lowest_possible_rtt_sin02']
pings['nue13_violated']=pings['nue13']<=pings['lowest_possible_rtt_nue13']
pings['nue17_violated']=pings['nue17']<=pings['lowest_possible_rtt_nue17']
pings['ewr01_violated']=pings['ewr01']<=pings['lowest_possible_rtt_ewr01']
pings['fnc01_violated']=pings['fnc01']<=pings['lowest_possible_rtt_fnc01']


violated=pings[(pings['ams02_violated'])|(pings['ams03_violated'])|(pings['ewr01_violated'])|
(pings['fnc01_violated'])|(pings['nue13_violated'])|(pings['nue17_violated'])|(pings['sin02_violated'])]

violated[['id']].to_csv('ctr_sol_violators.csv',index=False, header=False)
