# Signed GitHub releases and automatic updates

HostPanel publishes a signed GitHub Release for the deployable version in
`RELEASE_VERSION`. The signed source archive remains the immutable base; every
reviewed overlay revision must increase `RELEASE_VERSION` above the base
archive's `VERSION`. Reusing the same deployable version fails the release
build.

## Publication boundary

`.github/workflows/publish-release.yml` runs from protected `main`, verifies the
signed base source, enforces all release-gate issues, runs the complete
regression suite, builds the exact reviewed commit, and enters the protected
`hostpanel-release` environment only for signing and publication.

Release inputs include `RELEASE_VERSION`, updater/runtime code, the public
keyring, installers, packaging, and the release workflow itself. Existing tags
and releases are accepted only when their commit, exact asset set, signatures,
manifest, archive digest, and provenance all match the reviewed commit.

## Trusted update configuration

The update service does not source configuration as a process environment.
`/etc/hostpanel/update-agent.conf` is parsed through a descriptor-bound,
root-owned mode-0600 reader and accepts only these keys:

```ini
HP_UPDATE_REPOSITORY=1-vps/hostpanel
HP_UPDATE_CHANNEL=stable
HP_UPDATE_TOKEN_FILE=/etc/hostpanel/github-update.token
HP_UPDATE_REQUIRE_TOKEN=yes
HP_UPDATE_PUBLIC_KEY=/etc/hostpanel/update.pub
HP_UPDATE_KEYRING=/etc/hostpanel/update-keyring.json
HP_AUTO_UPDATE=yes
```

The token file must be root-owned, single-linked, mode `0600`, ASCII-only, and
contain no whitespace, including no trailing newline:

```bash
read -rsp 'GitHub update token: ' TOKEN; echo
printf '%s' "$TOKEN" | sudo tee /etc/hostpanel/github-update.token >/dev/null
unset TOKEN
sudo chown root:root /etc/hostpanel/github-update.token
sudo chmod 600 /etc/hostpanel/github-update.token
```

`stable` resolves GitHub's latest final release. `beta` inspects at most 20
GitHub releases and selects the highest valid signed prerelease version. Draft
flags, prerelease flags, tags, signed channel, and strict semantic-version form
must agree.

## Verification and transport

Every request and redirect must remain HTTPS and within the GitHub API/release
asset host set. Authorization is removed on cross-origin redirects. Duplicate
asset names, unsafe names, non-integer sizes, and advertised/downloaded size
mismatches fail closed.

The updater verifies, in order:

1. root ownership, exact mode, single link, stable descriptor/path identity,
   and bounded size for configuration, token, keyring, and key files;
2. a bounded keyring with SHA-256 key IDs and semantic activation/retirement
   windows;
3. the manifest signature using securely captured public-key bytes;
4. strict manifest shape, channel/version/tag/commit binding;
5. archive length, digest, and signature with the same key that signed the
   manifest;
6. archive path/type/mode/expansion bounds and extracted `VERSION`;
7. the existing reinstall, snapshot, health-check, and rollback path.

## Key rotation

Rotation is two-stage:

1. publish an old-key-signed release that adds the next public key to
   `releases/` and to `update-keyring.json`, with a future `activate_from`
   version while retaining the old key;
2. after that transition is installed, sign a later release with the new key;
3. keep an overlap window as needed, then set the old key's `retire_after`;
4. never remove the previous key before all supported clients have crossed the
   transition release.

The installer deploys the complete bounded keyring atomically with the updater.
The reinstall snapshot covers `/etc/hostpanel`, so rollback preserves the
previous trusted keyring.

## Manual operation and result codes

```bash
sudo /opt/hostpanel/tools/hostpanel-update --check
sudo /opt/hostpanel/tools/hostpanel-update --apply
sudo /opt/hostpanel/tools/hostpanel-update --dry-run
systemctl status hostpanel-update.timer
journalctl -u hostpanel-update.service
```

`--dry-run` downloads and verifies the complete manifest/archive/signature path
without requiring `--apply`. Exit `10` means a newer verified release is
available but was not applied. Exit `75` means another updater owns the lock.
The oneshot unit declares both expected states successful; verification and
installer failures remain service failures.

Status is written atomically to
`/var/lib/hostpanel/update-status.json`, including the signing key ID.
