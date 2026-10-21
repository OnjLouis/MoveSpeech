# Move Speech

Speech fixes for Ableton Move's Sampling, Slice, sample start/end controls and
step velocity.
Uses Move's existing screen-reader events, so browser speech and existing
Schwung speech setups can receive the restored text.

## Install

Run in a root SSH shell on Move:

```sh
python3 -E -s -B -c 'import urllib.request;exec(urllib.request.urlopen("https://raw.githubusercontent.com/OnjLouis/MoveSpeech/main/bootstrap.py",timeout=30).read())'
```

The installer checks a signed package, identifies the native firmware, builds
the patch on-device and asks before restarting Move. No compiler or Ableton
firmware download is required. Schwung is optional; voice settings and sets
are preserved.

Append `--check` to check compatibility without restarting, `--status` to
show the installed state, or `--uninstall` to restore unmodified Move.
Append `--yes` only when you want to approve a restart without a prompt.

Read [the HTML manual](https://onjlouis.github.io/MoveSpeech/) before installing. Exact firmware
and startup-script checks must pass; unknown builds are left unchanged.
After a firmware update, run the same command again. Compatibility with
every future firmware is not guaranteed.

Uninstall the patch before applying an official firmware update, then reinstall
afterward. The installer preserves the original native executable and refuses
unrecognised firmware instead of guessing at patch locations.

This is an unofficial modification, not an Ableton product. The native
executable is not overwritten or distributed here. Keep Move connected to
power during installation.

## Compatibility

Firmware 2.1.5b2 has been tested on a physical Move with Schwung, including
Sampling, Slice, loaded-sample controls and held-step velocity. The installer's
install, removal, reinstall, interrupted-connection and rollback paths have
also been tested on that configuration.

Profiles for 2.1.5b1 and 2.1.1, and the unwrapped stock-Move layout, have passed
offline checks but still need physical-device testing. Browser speech uses
Move's existing screen-reader interface; a separate end-to-end browser speech
test, a full power-cycle test and extended stability testing remain outstanding.

## Source

`bootstrap.py` downloads and verifies the installer package. `installer/`
contains the on-device patcher, signed firmware profiles and startup guard.
`src/` contains the small AArch64 speech hooks and text filter. Only the public
release-verification key is included; private signing material is not.

[Andre's software](https://onj.me/software)
