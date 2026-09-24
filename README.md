# RIPE Atlas Probes with Potentially Incorrect Reported Geolocation

This repo contains supplementary data for the paper [Rooting Out Incorrect RIPE Atlas Probe Geolocations](https://kizhikevich.github.io/assets/papers/ripe_paper.pdf)

This repo contains biweekly-updated (on the 1st and 15th of the month) lists of RIPE Atlas Probe IDs that likely have incorrect reported geolocation. Users may want to avoid using these probes for scheduling measurements or as measurement targets.

This repo also contains the execution pipeline that produces a list of violating RIPE Atlas Probe IDs.

## Pitfalls of a preprint

This paper used to be called [Trust, But Verify, Operator-Reported Geolocation](https://arxiv.org/html/2409.19109v1) with a different methodology, but similar results. Therefore, results generated before 2025-11-15 use an earlier methodology where [Ark](https://www.caida.org/projects/ark/) probes pinged Ripe Atlas probes as measurement targets. 

### Adding Methodology using DNS Root Servers

Results generated on or after 2025-11-15 use an updated output format that incorporates detection of potentially mis-reported RIPE Atlas probes using RIPE Atlas DNS Root measurements. The methodology is adapted from the ISC project: https://github.com/isc-projects/atlas-vis

The output now includes the following fields:

- `probe_id` — the RIPE Atlas probe id
- `violates_ark` — flagged by the Ark-based methodology
- `violates_rootdns` — flagged by the DNS Root–based methodology

## Running the execution pipeline yourself

We're still working on this. Stay tuned. 

