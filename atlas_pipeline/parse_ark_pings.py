#!/usr/bin/env python
# coding: utf-8

import urllib
import requests
import pandas as pd
import json
import bz2
import os
import numpy as np

probes = urllib.request.urlopen('https://ftp.ripe.net/ripe/atlas/probes/archive/meta-latest')
probes = json.load(bz2.open(probes))
probedf = pd.DataFrame(probes['objects'])
probedf = probedf[probedf.status == 1].dropna(subset=['address_v4'])
probedf = probedf[['id', 'address_v4', 'latitude', 'longitude']]

ark = pd.read_csv('ark_latlong.txt', delimiter=' ', names=['ark', 'lat', 'long'])
ark['latlong'] = list(zip(ark['lat'], ark['long']))


ark2atlas = pd.read_csv('ark_to_atlas.csv', delimiter=' ', names=['ark', 'address_v4', 'rtt'])
ark2atlas = ark2atlas.merge(probedf, on=['address_v4']).merge(ark, on=['ark'])


def calc_p_rtt_vectorized(lat1, lon1, lat2, lon2):
    # Convert latitude and longitude from degrees to radians
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    
    # Earth radius in km
    R = 6371
    return R * c / (199862638 / 1000 / 1000) * 2



ark2atlas['mp_rtt'] = calc_p_rtt_vectorized(ark2atlas['latitude'], ark2atlas['longitude'], ark2atlas['lat'], ark2atlas['long'])


ark_soi_vio = ark2atlas[ark2atlas.rtt < ark2atlas.mp_rtt]

atlas_ctr_vios = pd.read_csv('ctr_sol_violators.csv', names=['id'])

all_vios = set()
all_vios = set(atlas_ctr_vios['id']).union(ark_soi_vio['id'])

with open('probe_ids.txt', 'w') as f:
    for i in all_vios:
        f.write(str(i))
        f.write('\n')
        
