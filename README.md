# RIPE Atlas Probes with Potentially Incorrect Reported Geolocation

This repo contains supplementary data for the paper [Trust, But Verify, Operator-Reported Geolocation](https://arxiv.org/pdf/2409.19109)

This repo contains biweekly-updated (on the 1st and 15th of the month) lists of RIPE Atlas Probe IDs that likely have incorrect reported geolocation. Users may want to avoid using these probes for scheduling measurements or **as measurement targets**.

If the Ark measurement platform is experiencing increased latency during a particular measurement campaign due to higher load or other reasons, it may detect fewer probes with incorrect reported geolocation.

This repo also contains the execution pipeline that produces a list of violating RIPE Atlas Probe IDs.

### Adding Methodology using DNS Root Servers

Results generated on or after 2025-11-15 use an updated output format that incorporates detection of potentially mis-reported RIPE Atlas probes using RIPE Atlas DNS Root measurements. The methodology is adapted from the ISC project: https://github.com/isc-projects/atlas-vis

The output now includes the following fields:

- `probe_id` — the RIPE Atlas probe id
- `violates_ark` — flagged by the Ark-based methodology
- `violates_rootdns` — flagged by the DNS Root–based methodology

## Running the execution pipeline yourself

You first need to request access to the [Ark platform from CAIDA](https://www.caida.org/projects/ark/). Access is only available for academic researchers and a project description will be required for the request.

The folder `atlas_pipeline` contains the code. 

You will need to install the [CAIDA Scamper Tool](https://www.caida.org/catalog/software/scamper/) and its python packages to run the code.

In `run_atlas_pipeline.sh`, you will need to provide your directory containing Ark metadata at this line: `python3 bulkpinger.py --mode probe --scampers [YOUR DIRECTORY HERE] --targets  atlas_ips.txt`

The final output is `probe_ids.txt`

