# Changelog

## 0.3.3 (2026-10-14)

Security fix. Upgrade with `beakr update` or `uv tool upgrade beakr-cli`.

- The API key saved by `beakr auth login` and `beakr install` lives in
  `~/.beakr/config.json`. Earlier releases created `~/.beakr` and that file
  with default permissions (usually readable by every user on the machine).
  0.3.3 creates the directory `0700` and the file `0600`, writes the file
  atomically so the key is never briefly readable, and tightens a directory and
  file an earlier release created the first time it reads them.
- No other behavior changes; 0.3.3 talks to the same API as 0.3.2.

If other people have accounts on a machine where you used an earlier release,
consider revoking that API key in the Beakr web app and logging in with a new one.
