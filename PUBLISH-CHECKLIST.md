# Publishing adr-drift: checklist for Dan

Nothing here has been run for you.
(Round 5 added `forbid-transitive`, ADR `Scope:` and a few Windows fixes. They
are new features, not changes to old behaviour, so the version stays 0.3.0.) Nothing was uploaded, tagged or pushed.

## What to release

**Version 0.3.0.** PyPI only has 0.1.0 (checked 2026-11-08). The version is
already 0.3.0 in the code, in `adr_drift/__init__.py` (the one place it is
written), and the top of CHANGELOG.md says 0.3.0. 0.2.0 was never published,
so people jump from 0.1.0 to 0.3.0.

It is not a bug-fix release: it changes what existing users see (a retired
ADR stops being enforced; some layer globs match differently). The CHANGELOG
says so under "Changed". That is fine for a 0.x version, but do not call it
a patch.

## Check these first (a few minutes)

1. The author line on PyPI will read "Calas Automations Inc." (checked in
   the built file: `Author: Calas Automations Inc.`, no email) and the links
   point to `github.com/calasdan-sketch/adr-drift`. Change `authors` and
   `[project.urls]` in `pyproject.toml` if you want something else.
2. Open `https://github.com/calasdan-sketch/adr-drift` in a private browser
   window. If it asks you to sign in or says "404", the repository is
   private: then delete the whole `[project.urls]` block in `pyproject.toml`
   (and the install lines in the README) before building, because PyPI links
   to a private page go nowhere and tell strangers it exists. Nobody here
   could check this.
3. Look at the last CI run on the branch you will release from (Windows,
   macOS, Linux). **The author never ran this on Windows**; CI is the only
   evidence.
4. Decide the release branch: this work is on `expert-2`. Merge it to `main`
   in the way you normally do first.

## Commands (run in a clean checkout of the release commit)

```bash
python3 -m venv /tmp/rel && /tmp/rel/bin/pip install build twine
/tmp/rel/bin/python -m build                 # makes dist/ with a .whl and a .tar.gz
/tmp/rel/bin/twine check --strict dist/*     # both must say PASSED
# optional but wise: try it on the test index first
/tmp/rel/bin/twine upload --repository testpypi dist/*
# the real thing (uses your PyPI account or an API token; PyPI is free)
/tmp/rel/bin/twine upload dist/*
git tag v0.3.0 && git push origin v0.3.0
```

Cost: PyPI and TestPyPI are free. The upload cannot be undone for a given
version number (you can only "yank" it), so check the files in `dist/`
before the real upload.

Better than a password or a long-lived token: set up PyPI "trusted
publishing" for the GitHub repository (PyPI project settings, then a
workflow that uploads on a tag). Not wired up here.

Also worth one minute: after building, check the author in the file you are
about to upload:

```bash
unzip -p dist/*.whl '*METADATA' | grep -E '^(Name|Version|Author|License)'
```

It must print `Author: Calas Automations Inc.` and `Version: 0.3.0`.

## After it is up

1. `python3 -m venv /tmp/t && /tmp/t/bin/pip install adr-drift && /tmp/t/bin/adr-drift --version` should print `adr-drift 0.3.0`.
2. Open the PyPI page and check the README renders and the links work.
3. In `README.md`, the Install section says PyPI "had only 0.1.0" and that
   newer features need the git line. After 0.3.0 is up, change those lines to
   "pip install adr-drift" only, and remove the paragraph about 0.1.0.
4. Start a new "unreleased" heading in CHANGELOG.md and bump
   `adr_drift/__init__.py` for the next change.

## Known limits to be honest about on the PyPI page

- Not run on Windows or macOS by the author.
- `forbid-transitive` follows an import only when its name is unambiguous, and
  was tested on generated code, not on a large real repository.
- Regex time limit does not work on Windows (a hostile rule can hang there).
- JS/TS, Go, Rust, Java, Ruby, PHP are read by pattern, not by a real parser
  (README "Languages: exact support").
