#!/usr/bin/env python3
# Copyright 2024 Jon Seager (@jnsgruk)
# See LICENSE file for licensing details.


import json
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import timedelta
from logging import getLogger
from pathlib import Path
from typing import Dict, Iterable, Callable, Optional, Union

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


class Status(dict):
    def juju_status(self, application: str):
        """Mapping from unit name to active/idle, unknown/executing..."""
        units = self["applications"][application]["units"]
        return {u: units[u]["juju-status"]["current"] for u in units}

    def workload_status(self, application: str):
        """Mapping from unit name to active/idle, unknown/executing..."""
        units = self["applications"][application]["units"]
        return {u: units[u]["workload-status"]["current"] for u in units}

    def _check_workload_status_all(
        self,
        apps: Iterable[str],
        status: str,
        predicate_return: bool,
    ):
        if not apps:
            apps = self["applications"]

        for app in apps:
            wss = self.workload_status(app)
            if not wss:  # avoid vacuous quantification
                return not predicate_return

            if not all(us == status for us in wss.values()):
                return not predicate_return
        return predicate_return

    def _check_workload_status_any(
        self,
        apps: Iterable[str],
        status: str,
        predicate_return: bool,
    ):
        if not apps:
            apps = self["applications"]

        for app in apps:
            wss = self.workload_status(app)
            if not wss:  # avoid vacuous quantification
                return not predicate_return

            if any(us == status for us in wss.values()):
                return not predicate_return
        return predicate_return

    def all_active(self, *apps):
        """Return True if all units of these apps (or all apps) are in active status."""
        return self._check_workload_status_all(apps, "active", True)

    def all_blocked(self, *apps):
        """Return True if all units of these apps (or all apps) are in blocked status."""
        return self._check_workload_status_all(apps, "blocked", True)

    def any_error(self, *apps):
        """Return True if any unit of these apps (or all apps) are in error status."""
        return self._check_workload_status_any(apps, "error", False)

    def any_blocked(self, *apps):
        """Return True if any unit of these apps (or all apps) are in blocked status."""
        return self._check_workload_status_any(apps, "blocked", False)

    def any_waiting(self, *apps):
        """Return True if any unit of these apps (or all apps) are in waiting status."""
        return self._check_workload_status_any(apps, "waiting", False)

    def get_application_ip(self, app_name: str):
        return self["applications"][app_name]["public-address"]


class Juju:
    def __init__(self, model: str = None):
        self.model = model

    def model_name(self):
        return self.model or self.status()["model"]["name"]

    def status(self):
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
    ):
        """Wait for the stop condition to be met.

        Examples:
        >>> Juju("mymodel").wait(
        ...   stop=lambda s:s.all_active("foo"),
        ...   fail=lambda s:s.any_blocked("foo"),
        ...   timeout=2000)

        This will block until all "foo" units go to "active" status, and raise if any goes
         to "blocked" before the stop condition is met.
        """
        start = time.time()
        soak_time = _parse_time(soak)
        pass_counter = 0

        logger.info(f"Waiting for conditions; stop={stop}, fail={fail}")

        while time.time() - start < timeout:
            try:
                status = self.status()
                if stop:
                    if stop(status):
                        pass_counter += 1
                        if pass_counter >= soak_time.total_seconds():
                            return True

                    else:
                        pass_counter = 0

                if fail and fail(status):
                    raise WaitFailed()

            except WaitFailed:
                raise

            except Exception as e:
                logger.debug(f"error encountered while waiting: {e}")
                pass

            time.sleep(refresh_rate)

    def destroy_model(self, destroy_storage: bool = False):
        """Destroy this model."""
        args = ["destroy-model", "--no-prompt", self.model_name()]
        if destroy_storage:
            args.append("--destroy-storage")
        return self.cli(*args, add_model_flag=False)

    def cli(self, *args, add_model_flag: bool = True):
        if add_model_flag and "-m" not in args and self.model:
            args = [*args, "-m", self.model]

        args_ = list(map(str, ["/snap/bin/juju", *args]))
        logger.info(f"executing {' '.join(args_)!r}")

        proc = subprocess.run(
            args_,
            check=False,  # we want to expose the stderr on failure
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"NO_COLOR": "true"},
        )
        if proc.returncode and proc.stderr:
            logger.error(
                f"command {' '.join(args_)!r} exited with errors:\n{proc.stderr}"
            )
        proc.check_returncode()
        return proc