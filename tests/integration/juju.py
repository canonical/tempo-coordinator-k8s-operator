#!/usr/bin/env python3
# Copyright 2024 Jon Seager (@jnsgruk)
# See LICENSE file for licensing details.


import json
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import timedelta
from enum import Enum
from logging import getLogger
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from typing import Dict, Iterable, Callable, Optional, Union, Tuple, List

logger = getLogger("juju")

regex = re.compile(r"((?P<hours>\d+?)hr)?((?P<minutes>\d+?)m)?((?P<seconds>\d+?)s)?")


def _parse_time(delta: str) -> None | timedelta:
    parts = regex.match(delta)
    if not parts:
        return
    parts = parts.groupdict()
    time_params = {}
    for name, param in parts.items():
        if param:
            time_params[name] = int(param)
    return timedelta(**time_params)


class WaitFailed(Exception):
    """Raised when the ``Juju.wait()`` fail condition triggers."""


class WorkloadStatus(str, Enum):
    """Juju unit/app workload status."""

    active = "active"
    waiting = "waiting"
    maintenance = "maintenance"
    blocked = "blocked"
    error = "error"
    unknown = "unknown"


class AgentStatus(str, Enum):
    """Juju unit/app agent status."""

    idle = "idle"
    executing = "executing"
    allocating = "allocating"
    error = "error"


class Status(dict):
    def juju_status(self, application: str):
        """Mapping from unit name to active/idle, unknown/executing..."""
        units = self["applications"][application]["units"]
        return {u: units[u]["juju-status"]["current"] for u in units}

    def app_status(self, application: str) -> WorkloadStatus:
        """Application status."""
        return self["applications"][application]["application-status"]["current"]

    def workload_status(self, application: str) -> Dict[str, WorkloadStatus]:
        """Mapping from unit name to active/unknown/error..."""
        units = self["applications"][application]["units"]
        return {u: units[u]["workload-status"]["current"] for u in units}

    def agent_status(self, application: str) -> Dict[str, AgentStatus]:
        """Mapping from unit name to idle/executing/error..."""
        units = self["applications"][application]["units"]
        return {u: units[u]["juju-status"]["current"] for u in units}

    def _sanitize_apps_input(self, apps: Iterable[str]) -> Tuple[str, ...]:
        if not apps:
            return tuple(self["applications"])
        if isinstance(apps, str):
            # str is Iterable[str]...
            return (apps,)
        return tuple(apps)

    def _check_status_all(
        self, apps: Iterable[str], status: str, status_getter=Callable[["Status"], str]
    ):
        for app in self._sanitize_apps_input(apps):
            statuses = status_getter(app)
            if not statuses:  # avoid vacuous quantification
                return False

            if not all(us == status for us in statuses.values()):
                return False
        return True

    def _check_status_any(
        self, apps: Iterable[str], status: str, status_getter=Callable[["Status"], str]
    ):
        for app in self._sanitize_apps_input(apps):
            statuses = status_getter(app)
            if not statuses:  # avoid vacuous quantification
                # logically this should be false, but for consistency with 'all'...
                return True

            if any(us == status for us in statuses.values()):
                return True
        return False

    def all_workloads(self, apps: Iterable[str], status: WorkloadStatus):
        """Return True if all workloads of these apps (or all apps) are in this status."""
        return self._check_status_all(apps, status, status_getter=self.workload_status)

    def any_workload(self, apps: Iterable[str], status: WorkloadStatus):
        """Return True if any workload of these apps (or all apps) are in this status."""
        return self._check_status_any(apps, status, status_getter=self.workload_status)

    def all_agents(self, apps: Iterable[str], status: WorkloadStatus):
        """Return True if all agents of these apps (or all apps) are in this status."""
        return self._check_status_all(apps, status, status_getter=self.agent_status)

    def any_agent(self, apps: Iterable[str], status: WorkloadStatus):
        """Return True if any agent of these apps (or all apps) are in this status."""
        return self._check_status_any(apps, status, status_getter=self.agent_status)

    def get_application_ip(self, app_name: str):
        return self["applications"][app_name]["public-address"]


class JujuLogLevel(str, Enum):
    """Juju loglevels enum."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Juju:
    """Juju CLI wrapper for in-model operations."""

    def __init__(self, model: str = None):
        self.model = model

    def model_name(self):
        return self.model or self.status()["model"]["name"]

    def status(self) -> Status:
        args = ["status", "--format", "json"]
        result = self.cli(*args)
        return Status(json.loads(result.stdout))

    def config(self, app, config: Dict[str, bool | str]):
        args = ["config", app]
        for k, v in config.items():
            if isinstance(v, bool):
                args.append(f"{k}={str(v).lower()}")
            else:
                args.append(f"{k}={str(v)}")

        result = self.cli(*args)
        if result.stdout:
            # if used without args, returns the current config
            return json.loads(result.stdout)

    def model_config(self, config: Dict[str, bool | str] = None):
        args = ["model-config"]
        for k, v in config.items():
            if isinstance(v, bool):
                args.append(f"{k}={str(v).lower()}")
            else:
                args.append(f"{k}={str(v)}")

        result = self.cli(*args)
        if result.stdout:
            # if used without args, returns the current config
            return json.loads(result.stdout)

    @contextmanager
    def fast_forward(self, fast_interval: str = "5s", slow_interval: None | str = None):
        update_interval_key = "update-status-hook-interval"
        if slow_interval:
            interval_after = slow_interval
        else:
            interval_after = (self.model_config())[update_interval_key]

        self.model_config({update_interval_key: fast_interval})
        yield
        self.model_config({update_interval_key: interval_after})

    def deploy(
        self,
        charm: str | Path,
        *,
        alias: str | None = None,
        channel: str | None = None,
        config: None | Dict[str, str] = None,
        resources: None | Dict[str, str] = None,
        trust: bool = False,
        scale: int = 1,
    ):
        args = ["deploy", str(charm)]

        if alias:
            args = [*args, alias]

        if scale:
            args = [*args, "--num-units", scale]

        if channel:
            args = [*args, "--channel", channel]

        if config:
            for k, v in config.items():
                args = [*args, "--config", f"{k}={v}"]

        if resources:
            for k, v in resources.items():
                args = [*args, "--resource", f"{k}={v}"]

        if trust:
            args = [*args, "--trust"]

        return self.cli(*args)

    def integrate(self, requirer: str, provider: str):
        args = ["integrate", requirer, provider]
        return self.cli(*args)

    def disintegrate(self, requirer: str, provider: str):
        args = ["remove-relation", requirer, provider]
        return self.cli(*args)

    def scp(
        self, unit: str, origin: Union[str, Path], destination: Union[str, Path] = None
    ):
        args = [
            "scp",
            "-m",
            self.model,
            str(origin),
            f"{unit}:{destination or Path(origin).name}",
        ]
        return self.cli(*args)

    def ssh(self, unit: str, cmd: str):
        args = ["ssh", "-m", self.model, unit, cmd]
        return self.cli(*args)

    def run(self, app: str, action: str, params: Dict[str, str], unit_id: int = None):
        target = app + f"/{unit_id}" if unit_id is not None else app + "/leader"
        args = ["run", "--format", "json", target, action]

        for k, v in params.items():
            args.append(f"{k}={v}")

        act = self.cli(*args)
        result = json.loads(act.stdout)

        # even if you juju run foo/leader, the output will be for its specific ID: {"foo/0":...}
        return list(result.values())[0]

    def wait(
        self,
        timeout: int,
        soak: str = "10s",
        stop: Optional[Callable[[Status], bool]] = None,
        fail: Optional[Callable[[Status], bool]] = None,
        refresh_rate: float = 1.0,
        print_status_every: Optional[int] = 60,
    ):
        """Wait for the stop/fail condition to be met.

        Examples:
        >>> Juju("mymodel").wait(
        ...   stop=lambda s:s.all("foo", WorkloadStatus.active),
        ...   fail=lambda s:s.any("foo", WorkloadStatus.blocked),
        ...   timeout=2000)

        This will block until all "foo" units go to "active" status, and raise if any goes
         to "blocked" before the stop condition is met.
        """
        start = time.time()
        soak_time = _parse_time(soak)
        pass_counter = 0
        last_status_printed_time = (
            0  # number of seconds since the epoch, that is, very long ago
        )
        if not (stop or fail):
            raise ValueError(
                "pass a `stop` or a `fail` condition; "
                "else we don't know what to wait for."
            )

        logger.info(f"Waiting for conditions; stop={stop}, fail={fail}")

        def _display_status():
            print("current juju status:")
            print(self.cli("status", "--relations", quiet=True).stdout)

        try:
            while time.time() - start < timeout:
                try:
                    status = self.status()

                    # if the time elapsed since the last status-print is less than print_status_every,
                    # we print out the status.
                    if print_status_every is not None and (
                        (abs(last_status_printed_time - time.time()))
                        >= print_status_every
                    ):
                        last_status_printed_time = time.time()
                        _display_status()

                    if stop:
                        if stop(status):
                            pass_counter += 1
                            if pass_counter >= soak_time.total_seconds():
                                return True

                        else:
                            pass_counter = 0

                    if fail and fail(status):
                        raise WaitFailed("fail condition met during wait")

                except WaitFailed:
                    raise

                except Exception as e:
                    logger.debug(f"error encountered while waiting: {e}")
                    pass

                time.sleep(refresh_rate)
            raise TimeoutError(
                "timeout hit before any of the pass/fail conditions were met"
            )

        finally:
            # before we return, whether it's an exception or a True, we print out the status.
            _display_status()

    def debug_log(
        self,
        *,
        replay: bool = False,
        tail: bool = False,
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        include_module: Optional[List[str]] = None,
        exclude_module: Optional[List[str]] = None,
        include_label: Optional[List[str]] = None,
        exclude_label: Optional[List[str]] = None,
        ms: Optional[bool] = False,
        date: Optional[bool] = False,
        level: JujuLogLevel = JujuLogLevel.DEBUG,
    ) -> str:
        """Get the juju debug-log."""
        args = ["debug-log"]

        if tail:
            args.append("--tail")
        else:
            args.append("--no-tail")

        # text arguments
        for argname, value in (("level", level),):
            if value is not None:
                args.append(f"--{argname}={value}")

        # boolean flags
        _bool_str = {True: "true", False: "false"}
        for flagname, value in (
            ("ms", ms),
            ("date", date),
            ("replay", replay),
        ):
            if value is not None:
                args.append(f"--{flagname}={_bool_str[value]}")

        # include/exclude sequences
        for flagname, values in (
            ("include", include),
            ("exclude", exclude),
            ("include-module", include_module),
            ("exclude-module", exclude_module),
            ("include-label", include_label),
            ("exclude-label", exclude_label),
        ):
            if not values:
                continue
            for value in values:
                args.append(f"--{flagname}={value}")

        return self.cli(*args).stdout

    def destroy_model(self, destroy_storage: bool = False):
        """Destroy this model."""
        args = ["destroy-model", "--no-prompt", self.model_name()]
        if destroy_storage:
            args.append("--destroy-storage")
        return self.cli(*args, add_model_flag=False)

    def cli(
        self, *args, add_model_flag: bool = True, quiet: bool = False
    ) -> CompletedProcess:
        if add_model_flag and "-m" not in args and self.model:
            args = [*args, "-m", self.model]

        args_ = list(map(str, ["/snap/bin/juju", *args]))
        if not quiet:
            logger.info(f"executing {' '.join(args_)!r}")

        try:
            proc = subprocess.run(
                args_,
                check=False,  # we want to expose the stderr on failure
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"NO_COLOR": "true"},
            )
        except CalledProcessError:
            logger.error(f"command {' '.join(args_)!r} errored out")
            raise

        if proc.returncode:
            logger.info(f"command {' '.join(args_)!r} errored out.")
            logger.info(f"\tstdout:\n{proc.stdout}")
            logger.info(f"\tstderr:\n{proc.stderr}")

        # now we let it raise
        proc.check_returncode()
        return proc


if __name__ == "__main__":
    Juju().wait(
        timeout=2000,
        stop=lambda status: (status.app_status("traefik") == WorkloadStatus.error),
    )
