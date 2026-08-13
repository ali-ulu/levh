# Releasing

Two steps, and the second one is a `git push`.

## 1. Prepare the release (local)

```bash
python scripts/release.py --version 2.30.0
```

That rewrites the version everywhere it appears, rebuilds the frontend, syncs
the packaged dashboard, asserts every version site agrees, builds the wheel and
writes a source zip. The ordering matters: the dashboard is built *after* the
bump, so the packaged copy can never report an older version than the source.

Then write the `CHANGELOG.md` section for that version — the release notes are
taken from it verbatim, so whatever you write there is what people read on
GitHub.

Commit both, open a PR, merge it once CI is green.

## 2. Publish (push a tag)

```bash
git checkout main && git pull
git tag -a v2.30.0 -m "LEVH 2.30.0"
git push origin v2.30.0
```

`.github/workflows/publish.yml` takes it from there:

1. checks the tag matches the version in the tree — a tag on a commit that
   still says 2.29.0 fails here rather than publishing the wrong thing under
   the right name
2. runs `scripts/release.py --check`
3. builds the wheel and sdist, `twine check`s both
4. installs the wheel in a clean virtualenv, runs `levh --version`, and asserts
   the packaged dashboard is actually in it — a wheel that imports is not the
   same as a wheel that runs
5. creates the GitHub Release with the CHANGELOG section as its notes and the
   artifacts attached
6. uploads to PyPI

Nothing to run by hand, and nothing to remember in order.

## One-time setup

PyPI publishing uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/),
so there is no token stored in the repository. Set it up once:

1. <https://pypi.org/manage/project/levh/settings/publishing/>
2. Add a publisher: owner `ali-ulu`, repository `levh`, workflow
   `publish.yml`, environment `pypi`
3. In GitHub → Settings → Environments, create an environment named `pypi`
   (add a required reviewer if you want a human to approve each upload)

Until that exists the first five steps still run and the GitHub Release is
still created; only the PyPI job fails, and re-running it after setup is
enough.

## If something goes wrong

A tag can be moved before anyone depends on it:

```bash
git tag -d v2.30.0 && git push origin :refs/tags/v2.30.0
```

A PyPI upload cannot be undone — a version number is spent once it is
published, and a broken release is fixed by publishing the next one. That is
why the workflow installs and runs the wheel before it uploads.
