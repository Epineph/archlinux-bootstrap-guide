# Local bootstrap archives

Place official `archlinux-bootstrap-YYYY.MM.DD-x86_64.tar.zst` files here
(older `.tar.gz` archives are also recognized). Files in this directory are
ignored by Git, except this explanation. Keep the matching `.sig` alongside
each tarball for offline signature verification.

The newest **filename date**, not the modification time, is the default.
Undated official names sort after dated files. With several files, a terminal
gets a numbered menu; press Enter for the newest. Noninteractive use selects
the newest automatically. Use `--select N`, `--select newest`, or
`--tarball /absolute/path/archive.tar.zst` to be explicit.

`--download latest` checks the mirror's current release manifest and fetches
that release only if absent. It overrides the local selection. An explicit
HTTPS URL can be used instead. These actions require `--execute`.

