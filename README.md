# RIPE Atlas Probes with Potentially Incorrect Reported Geolocation

This repo contains weekly-updated lists of RIPE Atlas Probe IDs that likely have incorrect reported geolocation. Users may want to avoid using these probes for scheduling measurements or as measurement targets.

This repo also contains the execution pipeline that produces a list of violating RIPE Atlas Probe IDs.

## Running the execution pipeline yourself

You first need to request access to the [Ark platform from CAIDA](https://www.caida.org/projects/ark/). Access is only available for academic researchers and a project description will be required for the request.

The folder `atlas_pipeline` contains the code. 

You will need to install the [CAIDA Scamper Tool](https://www.caida.org/catalog/software/scamper/) and its python packages to run the code.

In `run_atlas_pipeline.sh`, you will need to provide your directory containing Ark metadata at this line: `python3 bulkpinger.py --mode probe --scampers [YOUR DIRECTORY HERE] --targets  atlas_ips.txt`

The final output is `probe_ids.txt`
