#!/usr/bin/python3
# coding: utf-8
"""Shell-command execution boundary for the install engine.

All shell-outs from InstallerEngine go through a CommandRunner instance,
so tests can substitute a recording/faking runner, and so every command
is logged in one place.  Semantics intentionally match what the engine
historically did with os.system / subprocess.getoutput / subprocess.Popen:
failures are logged but do not raise unless check=True is passed.
"""

import subprocess


class CommandError(Exception):
    """A command run with check=True exited non-zero."""

    def __init__(self, command, returncode, output=None):
        self.command = command
        self.returncode = returncode
        self.output = output
        super().__init__(
            f"Command failed (rc={returncode}): {command}"
        )


class CommandRunner:
    """Runs shell commands for the install engine."""

    def __init__(self, log=print):
        self.log = log

    def run(self, command, check=False):
        """Run a shell command; return its exit code (os.system replacement).

        Non-zero exit codes are logged.  With check=True a non-zero exit
        raises CommandError instead of being silently tolerated.
        """
        self.log("EXEC: %s" % command)
        returncode = subprocess.call(command, shell=True)
        if returncode != 0:
            self.log("EXEC failed (rc=%d): %s" % (returncode, command))
            if check:
                raise CommandError(command, returncode)
        return returncode

    def output(self, command, check=False):
        """Return combined stdout+stderr of a shell command, without the
        trailing newline (subprocess.getoutput replacement)."""
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            self.log("EXEC failed (rc=%d): %s" % (result.returncode, command))
            if check:
                raise CommandError(command, result.returncode, result.stdout)
        data = result.stdout or ""
        if data.endswith("\n"):
            data = data[:-1]
        return data

    def chroot(self, command, check=False, target="/target"):
        """Run a command inside the target chroot."""
        command = command.replace('"', "'").strip()
        return self.run(
            'chroot %s/ /bin/sh -c "%s"' % (target.rstrip("/"), command),
            check=check,
        )

    def popen(self, command):
        """Start a shell command with captured, line-readable output.

        For callers that stream output as it is produced (rsync progress,
        exec_cmd).  stderr is folded into stdout, matching the engine's
        historical Popen usage.
        """
        self.log("EXEC (stream): %s" % command)
        return subprocess.Popen(
            command,
            shell=True,
            encoding="utf-8",
            errors="ignore",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
