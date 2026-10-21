"""Short-lived startup guard; never runs as a persistent daemon."""

import contextlib
import sys

from device import Device, secure


def main():
    device = Device()
    device.prepare()
    log = device.root / 'startup.log'
    if log.exists():
        secure(log)
        if log.stat().st_size > 16384:
            log.replace(device.root / 'startup.previous.log')
    with log.open('a', buffering=1) as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        if sys.argv[1:] == ['activate']:
            if device.activate():
                print('Verified Move Speech enabled', flush=True)
        elif sys.argv[1:] == ['deactivate']:
            device.deactivate(device.read_state())
        else:
            raise RuntimeError('Use activate or deactivate')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Move Speech startup: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
