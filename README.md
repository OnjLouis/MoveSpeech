# Move Speech

Speech fixes for Ableton Move's Sampling, Slice, sample start/end controls and
step velocity, plus Wi-Fi password character selection.
Loaded-sample Start and audio-track Start include their changing positions as
percentages of the complete sample.
The password selector announces letters, numbers, punctuation and its action
controls without reading back the complete password. Separate confirmation
of character insertion and deletion is not yet provided.
Uses Move's existing screen-reader events, so browser speech and existing
Schwung speech setups can receive the restored text.

## Install

Run in a root SSH shell on Move:

```sh
wget -qO- https://onjlouis.github.io/MoveSpeech/install.sh | sh
```

**Security notice:** Move's built-in `wget` does not verify HTTPS certificates.
This short command trusts your network to deliver the initial shell script.
Signed package checks cannot protect a replaced first script. On an untrusted
network, use the certificate-checked Python option instead:

```sh
python3 -E -s -B -c 'import urllib.request;exec(urllib.request.urlopen("https://raw.githubusercontent.com/OnjLouis/MoveSpeech/main/bootstrap.py",timeout=30).read())'
```

The installer checks a signed package, identifies the native firmware, builds
the patch on-device and asks before restarting Move. No compiler or Ableton
firmware download is required. Schwung is optional; voice settings and sets
are preserved.

For a shell check without restarting, end the short command with
`| sh -s -- --check`. To remove the patch, use `| sh -s -- --uninstall`.
Use `| sh -s -- --status` to show the installed state, or `| sh -s -- --yes`
only when you want to approve a restart without a prompt.

For the Python option, append the same option after its closing single quote.
Both methods use the same signed installer and preserve confirmation prompts.
Inspect the [shell entry](install.sh) and [Python bootstrap](bootstrap.py)
before running them if you prefer.

Version 1.0.1 and later also install an offline shell entry:

```sh
sh /data/onj-move-accessibility/install.sh --uninstall
```

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
Sampling, Slice, loaded-sample controls, held-step velocity and the Wi-Fi
password character selector. The installer's
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
