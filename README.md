# live-installer

The installer used by Linux Mint and LMDE. It runs as a GTK wizard for
interactive desktop installs, and can also run **unattended** from a YAML
answer file for labs, fleets, and OEM/imaging pipelines.

## Layout

```
usr/bin/                       entry points (live-installer, oem-config, …)
usr/lib/live-installer/        the installer
  main.py                      GTK wizard (interactive front-end)
  installer.py                 install engine (GUI-agnostic)
  partitioning.py              disk / partition handling
  auto_installer.py            unattended front-end (answer-file driven)
  schema.py                    answer-file schema + validation
  diskmatch.py                 stable-attribute disk selection
  commandrunner.py             single shell-out boundary (logging, secrets)
docs/automated-install.md      unattended-install user guide
tests/                         unit + integration tests, and TESTING.md
```

## Unattended installation

Boot the live medium with an answer file on the kernel command line:

```
live-installer.auto=/cdrom/install.yaml
```

See **[docs/automated-install.md](docs/automated-install.md)** for the
answer-file reference, delivery options, examples, and security notes.
The interactive GUI is unchanged when no answer file is supplied.

## Development & testing

Unit tests need only Python; integration tests perform real installs in a
VM. See **[tests/TESTING.md](tests/TESTING.md)** for the architecture, how
to run and extend the suite, and the known traps.

```sh
# unit tests
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements-test.txt
.venv/bin/pytest tests/unit
```

## License

GPL-2+. See [COPYING](COPYING) and `debian/copyright`.
