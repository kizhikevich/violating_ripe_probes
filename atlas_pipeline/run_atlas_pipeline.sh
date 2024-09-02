#!/bin/bash

rm output/*

python3 atlas_ctr_pings.py

python3 parse_atlas_ctr_pings.py

echo "Finished Atlas central server compute"

python3 get_connected_atlas_probes.py

echo "starting bulkpinger"

python3 bulkpinger.py --mode probe --scampers [YOUR ARK DIRECTORY HERE] --targets  atlas_ips.txt

echo "finished bulkpinger, parsing output"

python3 bulkpinger.py --mode dump output/* > ark_to_atlas.csv

python3 parse_ark_pings.py

echo "all done, updating git repo"


