# Releasing Core Memory 2

A release publishes the `core-memory` command to PyPI as the distribution
`core-memory`: one wheel per platform, each carrying the compiled engine,
and an sdist. Nothing is published to crates.io.

The workflow is
[`.github/workflows/release-pypi.yml`](.github/workflows/release-pypi.yml).
A pushed tag starts it. It builds and checks everything, then waits for the
owner's approval before anything is uploaded.

## Once, before the first release

These are the owner's settings; a contributor cannot change them.

1. **An environment that waits for approval.** In the repository's
   Settings, under Environments, create `pypi-v2` and add the owner as a
   required reviewer. The publish job runs in that environment. Without a
   required reviewer it would upload as soon as the builds pass, without
   asking anyone.
2. **A trusted publisher on PyPI.** On pypi.org, under the `core-memory`
   project's Publishing settings, add a GitHub trusted publisher with:
   - owner `JohnnyFiv3r`
   - repository `Core-Memory`
   - workflow `release-pypi.yml`
   - environment `pypi-v2`

   The workflow then uploads with a short-lived token that PyPI issues to
   that workflow in that environment. No token is stored in the
   repository.
3. **The 1.x workflow off.** Under Actions, open "Publish to PyPI" (the
   1.x `publish-pypi.yml`) and choose "Disable workflow". It published on
   every GitHub release, using the workflow file at the release's tag, so a
   release made on a 1.x tag would otherwise try to publish 1.x again.

## Each release

1. Set the version in `[workspace.package]` and the `=` pins under
   `[workspace.dependencies]` in [`Cargo.toml`](Cargo.toml), run
   `cargo check --workspace` so `Cargo.lock` follows, and merge that through
   a pull request. Cargo's `2.0.0-alpha.1` is published to PyPI as
   `2.0.0a1`, `2.0.0-rc.1` as `2.0.0rc1`, and `2.0.0` as itself.
2. Tag the merged commit with `v` and the Cargo version, and push the tag:

   ```bash
   git tag -a v2.0.0-alpha.1 -m "Core Memory 2.0.0-alpha.1"
   git push origin v2.0.0-alpha.1
   ```

   The workflow refuses a tag that does not match the Cargo version.
3. The workflow builds:
   - Linux wheels (manylinux, x86_64 and aarch64);
   - macOS wheels (Apple silicon and Intel);
   - the sdist.

   It installs the Linux x86_64 and macOS Apple silicon wheels and runs
   `core-memory --version` on each.
4. The publish job then waits. The owner opens the run under Actions,
   checks the build jobs and the list of files it will upload, and chooses
   "Review deployments", then `pypi-v2`, then "Approve and deploy".
5. Check the release:

   ```bash
   uv tool install core-memory==2.0.0a1
   core-memory --version
   ```

A pre-release (`a`, `b`, `rc`) is installed only by someone who names it or
asks for pre-releases. `pip install core-memory` keeps installing the newest
final release, which is 1.1.2 until 2.0.0 is out.

## If something goes wrong

- **A build fails:** nothing was uploaded. Fix it through a pull request,
  delete the tag (`git push --delete origin v2.0.0-alpha.1`), and tag the
  fixed commit with the same version.
- **An upload stopped part way:** PyPI keeps the files that arrived and
  never takes a file of that version again. Move the version on and
  release again.
- **A bad release was published:** yank it on PyPI. Yanking keeps it for
  anyone who pins it exactly and hides it from everyone else. Then release
  a fixed version.
- **The publish job fails with an OIDC or trusted-publisher error:** the
  PyPI publisher's owner, repository, workflow file name or environment does
  not match the four values above.
