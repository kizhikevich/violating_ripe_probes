#!/usr/bin/env python3

import argparse
import datetime
import random
import re
from math import gcd
from scamper import ScamperAddr, ScamperFile, ScamperCtrl, ScamperInst, ScamperDealias, ScamperDealiasProbedef

class _VantagePoint:
    def __init__(self, name: str):
        self.name = name
        self.remote = None
        self.out = None
        self.inst = None
        self.coprime = 1
        self.runval = 1
        self.off = 0
        self.rounds = 0

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if self.name == other.name:
            return True
        return False

@staticmethod
def _arkname(name: str) -> str:
    match = re.match("^([a-z]{3}\\d*-[a-z]{2})\\.ark$", name)
    if match:
        return match.group(1)
    return name

@staticmethod
def _insteof(ctrl: ScamperCtrl, inst: ScamperInst, vps: list[_VantagePoint]):
    name = _arkname(inst.name)
    print(f"{datetime.datetime.now()} {name} finished")
    if name in vps:
        del vps[name]
    print(f"{len(vps)} VPs still working")

@staticmethod
def _doit(ctrl: ScamperCtrl, inst: ScamperInst, batch: list[ScamperAddr]):
    if len(batch) == 0:
        inst.done()
    else:
        ctrl.do_radargun(inst=inst, rounds=4,
                         wait_probe = datetime.timedelta(milliseconds=20), #50 pps
                         wait_round = datetime.timedelta(seconds=1),
                         wait_timeout = datetime.timedelta(seconds=2),
                         probedefs=[ScamperDealiasProbedef('icmp-echo', ttl=255)],
                         addrs = batch)

@staticmethod
def _getips(vp: _VantagePoint, targets: list[ScamperAddr], n: int):
    targetc = len(targets)
    out = []
    while vp.off < targetc:
        out.append(targets[vp.runval])
        vp.runval += vp.coprime
        if vp.runval >= targetc:
            vp.runval -= targetc
        vp.off += 1
        if len(out) == n:
            break
    return out

@staticmethod
def _mode_dump(args: argparse.ArgumentParser):
    for infile in args.files:
        warts = ScamperFile(infile, filter_types=[ScamperDealias])
        for obj in warts:
            if not obj.is_radargun():
                continue
            vp = _arkname(obj.list.monitor)
            for probe in obj.probes():
                for reply in probe.replies():
                    if not reply.is_from_target():
                        continue
                    rtt = reply.rx - probe.tx
                    print(f"{vp} {probe.probedef.dst} {rtt.total_seconds()*1000:3.1f}")
        warts.close()

def _mode_probe(args: argparse.ArgumentParser):
    batchsize = 50

    if args.targets is None:
        print("need targets file")
        return
    if args.scampers is None:
        print("need scampers location")
        return

    # load the targets out of the input file
    targets = []
    with open(args.targets) as infile:
        for line in infile:
            try:
                targets.append(ScamperAddr(line.rstrip('\n')))
            except ValueError:
                pass

    # calculate a set if coprimes that will allow us to select each
    # item in the targets list in a per-VP randomized order.
    coprimes = []
    n = len(targets)
    for i in range(int(n/2), n):
        x = gcd(i, n)
        if x == 1:
            coprimes.append(i)

    # ping from all of the available VPs.
    vps = {}
    ctrl = ScamperCtrl(remote_dir=args.scampers, eofcb=_insteof, param=vps)
    tsstr = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for inst in ctrl.instances():
        name = _arkname(inst.name)
        if name == 'sjj-ba':
            inst.done()
            continue
        vp = _VantagePoint(name)
        vp.inst = inst
        vp.coprime = random.choice(coprimes)
        vp.runval = random.randrange(0, n)
        vp.out = ScamperFile(f"output/{name}.{tsstr}.bulkpinger.warts", 'w')
        vps[name] = vp
        _doit(ctrl, vp.inst, _getips(vp, targets, batchsize))

    print(f"{datetime.datetime.now()} starting with {len(vps)} VPs")

    while not ctrl.is_done():
        obj = None
        try:
            obj = ctrl.poll(timeout=datetime.timedelta(seconds=15*60))
        except Exception as exc:
            print(exc)
        if obj is None:
            if not ctrl.is_done():
                print("f{datetime.datetime.now()} nothing rxd in time, exiting")
            break
        vp = vps[_arkname(obj.inst.name)]
        vp.out.write(obj)
        if isinstance(obj, ScamperDealias):
            vp.rounds += 1
            print(f"{datetime.datetime.now()} {vp.name} round {vp.rounds}")
            _doit(ctrl, vp.inst, _getips(vp, targets, batchsize))

@staticmethod
def _main():

    parser = argparse.ArgumentParser(description='bulk pinger')
    parser.add_argument('files',
                        metavar = 'file',
                        nargs = argparse.REMAINDER,
                        help = 'collected data to process')
    parser.add_argument('--mode',
                        dest = 'mode',
                        choices = ['dump', 'probe'],
                        required = True,                        
                        help = 'mode to use')
    parser.add_argument('--targets',
                        dest = 'targets',
                        default = None,
                        help = 'target file to probe, 1 address per line')
    parser.add_argument('--scampers',
                        dest = 'scampers',
                        default = None,
                        help = 'directory to find scamper VPs')

    args = parser.parse_args()

    if args.mode == 'dump':
        _mode_dump(args)
    elif args.mode == 'probe':
        _mode_probe(args)
    
if __name__ == "__main__":
    _main()
