# Signed GitHub releases and automatic updates

HostPanel publishes a signed GitHub Release when a new signed source version is
merged to `main`. Installed servers poll the private repository every five
minutes and apply a newer signed stable release.

## Release publication

The workflow `.github/workflows/publish-release.yml` runs only after the signed
source archive, its signature, or `SHA256SUMS` changes on `main`. It has no
manual branch-selectable dispatch.

Publication is split into two security boundaries:

1. the `verify` job has read-only repository access, verifies the signed source,
   checks release gates, runs the full regression suite, and builds a test
   update archive;
2. the `publish` job runs only after verification, enters the protected
   `hostpanel-release` environment, rebuilds from the same exact commit, signs
   the archive and canonical manifest, and receives write access only while it
   creates the tag and GitHub Release.

The workflow also handles a partial previous failure. If the tag exists but the
Release does not, publication resumes only when the existing lightweight tag
points to the exact tested commit. An existing tag that points elsewhere is a
hard failure.

Before any new release is signed, GitHub issues #7 and #14 must both be closed.
This makes provider-backed acceptance and native-language approval technical
release gates rather than documentation-only reminders.

### Required GitHub environment

Create an environment named `hostpanel-release` and configure all of the
following outside repository code:

- allow deployments from protected `main` only;
- require independent reviewer approval;
- prevent self-review where supported;
- store `HOSTPANEL_RELEASE_PRIVATE_KEY` as an **environment secret**, not a
  repository-wide secret;
- remove any older repository-level copy of that secret.

`HOSTPANEL_RELEASE_PRIVATE_KEY` must contain the PEM Ed25519 private key matching
`releases/update.pub`. The private key must never be committed to the repository
or copied to an installed server.

To publish a version, add the newly signed source archive and signature, replace
`SHA256SUMS`, update release notes, and merge through the protected `main`
branch. Reusing an existing version never creates a second release.

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
**Contents** access to this repository and store it in a root-owned mode-0600
file:

```bash
sudo install -o root -g root -m 600 /dev/null /etc/hostpanel/github-update.token
read -rsp 'GitHub update token: ' TOKEN; echo
printf '%s\n' "$TOKEN" | sudo tee /etc/hostpanel/github-update.token >/dev/null
unset TOKEN
sudo chown root:root /etc/hostpanel/github-update.token
sudo chmod 600 /etc/hostpanel/github-update.token
sudo systemctl start hostpanel-update.service
```

A public repository does not require the token file; set
`HP_UPDATE_REQUIRE_TOKEN=no` only when the Release assets are intentionally
public.

## Manual operation

```bash
sudo /opt/hostpanel/tools/hostpanel-update --check
sudo /opt/hostpanel/tools/hostpanel-update --apply
sudo /opt/hostpanel/tools/hostpanel-update --apply --dry-run
systemctl status hostpanel-update.timer
journalctl -u hostpanel-update.service
```

The updater verifies, in order:

- GitHub release metadata and stable channel;
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

## Release safety gate

Do not close issue #7 until provider-backed installation, reboot, external
web/DNS/mail, backup/restore and controlled rollback evidence is attached.
Do not close issue #14 until named native reviewers have approved all three
locale catalogs and rendered states.

The workflow automates packaging, verification and publication. It intentionally
cannot replace these external approvals.
