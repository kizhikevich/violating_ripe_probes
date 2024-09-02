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

probedf[['address_v4']].drop_duplicates().to_csv('atlas_ips.txt', index=False, header=False)