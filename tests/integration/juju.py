#!/usr/bin/env python3
# Copyright 2024 Jon Seager (@jnsgruk)
# See LICENSE file for licensing details.


import json
import subprocess
import time
from pathlib import Path
from typing import Dict, List


class Juju:
    @classmethod
    def model_name(cls):
        return cls.status()["model"]["name"]

    @classmethod
    def status(cls):
        args = ["status", "--format", "json"]
        result = cls.cli(*args)
        return json.loads(result.stdout)

    @classmethod
    def config(cls, app, config: Dict[str, bool | str]):
        args = ["config", app]
        for k, v in config:
            if isinstance(v, bool):
                args.append(f"{k}={str(v).lower()}")
            else:
                args.append(f"{k}={str(v)}")

        result = cls.cli(*args)
        return json.loads(result.stdout)

    @classmethod
    def deploy(
        cls,
        charm: str | Path,
        *,
        alias: str | None = None,
        channel: str | None = None,
        config: None | Dict[str, str] = None,
        resources: None | Dict[str, str] = None,
        trust: bool = False,
            scale:int=1
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

        return cls.cli(*args)

    @classmethod
    def integrate(cls, requirer: str, provider: str):
        args = ["integrate", requirer, provider]
        return cls.cli(*args)

    @classmethod
    def disintegrate(cls, requirer: str, provider: str):
        args = ["remove-relation", requirer, provider]
        return cls.cli(*args)

    @classmethod
    def run(cls, unit: str, action: str, params: Dict[str,str]):
        args = ["run", "--format", "json", unit, action]

        for k, v in params:
            args.append(f"{k}={v}")


        act = cls.cli(*args)
        result = json.loads(act.stdout)
        return result[unit]["results"]

    @classmethod
    def wait_for_idle(cls, applications: List[str], timeout: int):
        start = time.time()
        while time.time() - start < timeout:
            try:
                results = []
                for a in applications:
                    results.extend(cls._unit_statuses(a))
                if set(results) != {"active/idle"}:
                    raise Exception
                else:
                    break
            except Exception:
                time.sleep(1)

    @classmethod
    def cli(cls, *args):
        proc = subprocess.run(
            ["/snap/bin/juju", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"NO_COLOR": "true"},
        )
        return proc

    @classmethod
    def _unit_statuses(cls, application: str):
        units = cls.status()["applications"][application]["units"]
        return [
            f"{units[u]['workload-status']['current']}/{units[u]['juju-status']['current']}"
            for u in units
        ]