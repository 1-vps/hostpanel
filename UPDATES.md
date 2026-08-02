# Signed GitHub releases and automatic updates

HostPanel can publish a signed GitHub Release whenever a new signed source
version is merged to `main`. Installed servers poll the repository every five
minutes and apply a newer signed stable release.

## Release publication

The workflow `.github/workflows/publish-release.yml` runs when the signed source
archive, its signature, or `SHA256SUMS` changes on `main`. If the tag
`v<VERSION>` does not exist, it:

1. verifies the existing signed source archive;
2. runs the full regression suite;
3. builds a deterministic `hostpanel-v<VERSION>-update.tar.gz`;
4. signs the archive and canonical manifest;
5. creates the version tag at the tested commit;
6. publishes all assets in a GitHub Release.

Configure the repository Actions secret `HOSTPANEL_RELEASE_PRIVATE_KEY` with
the PEM Ed25519 private key matching `releases/update.pub`. The private key must
never be committed to the repository.

To publish a version, add the newly signed source archive and signature, replace
`SHA256SUMS`, update release notes, and merge to `main`. Reusing an existing
version does not create a second release.

## Server configuration

The installer enables `hostpanel-update.timer`. Its defaults are stored in
`/etc/hostpanel/update-agent.conf`:

```ini
HP_UPDATE_REPOSITORY=1-vps/hostpanel
HP_UPDATE_CHANNEL=stable
HP_UPDATE_TOKEN_FILE=/etc/hostpanel/github-update.token
HP_UPDATE_REQUIRE_TOKEN=yes
HP_UPDATE_PUBLIC_KEY=/etc/hostpanel/update.pub
HP_AUTO_UPDATE=yes
```

For a private GitHub repository, create a fine-grained token with read-only
**Contents** access to this repository and store it root-only:

```bash
sudo install -o root -g root -m 600 /dev/null /etc/hostpanel/github-update.token
read -rsp 'GitHub update token: ' TOKEN; echo
printf '%s\n' "$TOKEN" | sudo tee /etc/hostpanel/github-update.token >/dev/null
unset TOKEN
sudo systemctl start hostpanel-update.service
```

A public repository does not require the token file; set
`HP_UPDATE_REQUIRE_TOKEN=no`.

## Manual operation

```bash
sudo /opt/hostpanel/tools/hostpanel-update --check
sudo /opt/hostpanel/tools/hostpanel-update --apply
sudo /opt/hostpanel/tools/hostpanel-update --apply --dry-run
systemctl status hostpanel-update.timer
journalctl -u hostpanel-update.service
```

The updater verifies, in order:

- GitHub release metadata and channel;
- the manifest signature against `/etc/hostpanel/update.pub`;
- manifest schema, semantic version, tag and commit;
- archive size and SHA-256;
- archive signature;
- every archive path, type, mode and extraction bound;
- the extracted `VERSION`;
- the existing reinstall and rollback path.

Status is written atomically to `/var/lib/hostpanel/update-status.json`.
The installer snapshot and rollback mechanisms remain responsible for restoring
the previous installation if an update fails.
