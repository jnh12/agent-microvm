"""Bare-boto3 infra provisioner.

Orchestrates the resource handlers in :mod:`agent_sandbox.infra.resources`,
records what it created in :mod:`agent_sandbox.infra.state`, and applies the
reuse-or-create rules from :class:`agent_sandbox.infra.config.InfraConfig`.

Public surface backs the ``asb infra`` commands: ``up``, ``plan`` (preview),
``destroy``, ``refresh``, ``outputs`` -- plus a module-level ``read_outputs``
used by the CLI to auto-wire image/role.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_sandbox.infra import resources as R
from agent_sandbox.infra.config import InfraConfig
from agent_sandbox.infra.state import InfraStateStore, StackState

# A progress reporter: called with a human-readable message per step.
Reporter = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


class Provisioner:
    """Provisions/destroys agent-sandbox infra with boto3, tracking JSON state."""

    def __init__(self, cfg: InfraConfig, store: InfraStateStore | None = None) -> None:
        cfg.validate()
        self._cfg = cfg
        self._store = store or InfraStateStore()
        self._clients: dict[str, Any] = {}

    # -- client helpers ----------------------------------------------------

    def _client(self, service: str):
        if service not in self._clients:
            import boto3

            self._clients[service] = boto3.client(
                service, region_name=self._cfg.region
            )
        return self._clients[service]

    def _account_id(self) -> str:
        return self._client("sts").get_caller_identity()["Account"]

    # -- up ----------------------------------------------------------------

    def up(self, *, rebuild: bool = False, report: Reporter | None = None) -> dict[str, Any]:
        emit = report or _noop
        cfg = self._cfg
        emit(f"Provisioning stack '{cfg.project}/{cfg.stack}' in {cfg.region}")
        state = self._store.load(cfg.project, cfg.stack)
        iam = self._client("iam")
        s3 = self._client("s3")
        mv = self._client("lambda-microvms")

        # -- IAM role: reuse or create ------------------------------------
        role_managed = not cfg.role.reuse
        if cfg.role.reuse:
            role_arn = cfg.role.arn
            role_name = cfg.role.name
            emit(f"IAM role: reusing {role_arn}")
            state.set_resource("role", "iam-role", role_name, managed=False,
                               attrs={"arn": role_arn})
        else:
            emit(f"IAM role: creating '{cfg.role.name}' (+ logs policy)")
            role = R.ensure_role(
                iam,
                name=cfg.role.name,
                region=cfg.region,
                account_id=self._account_id(),
                extra_policy_arns=cfg.role.extra_policy_arns,
            )
            role_arn = role["arn"]
            role_name = role["name"]
            state.set_resource("role", "iam-role", role_name, managed=True,
                               attrs={"arn": role_arn})
        # Persist incrementally so a later failure still leaves a destroyable
        # record of what we created.
        self._store.save(state)

        # -- S3 build bucket: reuse or create -----------------------------
        if cfg.bucket.reuse:
            bucket_name = cfg.bucket.name
            bucket_arn = f"arn:aws:s3:::{bucket_name}"
            emit(f"S3 build bucket: reusing '{bucket_name}'")
            state.set_resource("bucket", "s3-bucket", bucket_name, managed=False)
        else:
            bucket_name = self._bucket_name(state)
            emit(f"S3 build bucket: creating '{bucket_name}'")
            bucket = R.ensure_bucket(s3, name=bucket_name, region=cfg.region)
            bucket_name = bucket["name"]
            bucket_arn = bucket["arn"]
            state.set_resource("bucket", "s3-bucket", bucket_name, managed=True)
        self._store.save(state)

        # Grant the (managed) build role read access to the artifact bucket.
        if role_managed:
            emit("IAM role: granting read access to the build bucket")
            R.attach_s3_read(iam, role_name=role_name, bucket_arn=bucket_arn)

        # -- Upload the guest image zip -----------------------------------
        key = f"microvm-images/{cfg.image.name}.zip"
        emit(f"Guest image: uploading ./{cfg.image.guest_dir} -> s3://{bucket_name}/{key}")
        code_uri = R.upload_guest(
            s3, bucket=bucket_name, key=key, guest_dir=cfg.guest_dir_abs
        )
        state.set_resource("guest_object", "s3-object", key, managed=True,
                           attrs={"bucket": bucket_name})
        self._store.save(state)

        # -- MicroVM image ------------------------------------------------
        prev_image = state.get_resource("image")
        if not rebuild and R._image_active_version(mv, cfg.image.name):
            emit(f"MicroVM image: reusing active image '{cfg.image.name}'")
        else:
            emit(
                f"MicroVM image: building '{cfg.image.name}' from snapshot "
                "(this can take a few minutes)..."
            )
        image = R.ensure_image(
            mv,
            name=cfg.image.name,
            build_role_arn=role_arn,
            code_uri=code_uri,
            base_image_arn=cfg.image.base_image_arn or None,
            base_image_version=cfg.image.base_image_version or None,
            rebuild=rebuild,
        )
        emit(f"MicroVM image: active version {image['version']}")
        image_managed = image["created"] or bool(prev_image and prev_image.managed)
        state.set_resource("image", "microvm-image", image["arn"],
                           managed=image_managed,
                           attrs={"version": image["version"]})

        outputs: dict[str, Any] = {
            "image_arn": image["arn"],
            "execution_role_arn": role_arn,
            "build_bucket": bucket_name,
        }

        # -- Optional VPC egress network connector ------------------------
        if cfg.network.enabled:
            egress = cfg.network.egress
            if egress.reuse:
                emit(f"Egress connector: reusing {egress.connector_arn}")
                state.set_resource(
                    "network_connector", "network-connector",
                    egress.connector_arn, managed=False,
                )
                outputs["egress_network_connector_arn"] = egress.connector_arn
            else:
                ec2 = self._client("ec2")
                vpc_id = egress.vpc_id or R.default_vpc_id(ec2)
                if egress.reuse_sg:
                    sg_id = egress.security_group_id
                    emit(f"Egress connector: reusing security group {sg_id} (vpc {vpc_id})")
                    state.set_resource("security_group", "ec2-sg", sg_id, managed=False)
                else:
                    emit(f"Egress connector: creating egress-only SG (vpc {vpc_id})")
                    sg_id = R.ensure_security_group(ec2, vpc_id=vpc_id, name=egress.name)
                    state.set_resource("security_group", "ec2-sg", sg_id, managed=True)
                if egress.subnet_ids:
                    subnet_ids = R.resolve_subnet_ids(ec2, egress.subnet_ids, vpc_id)
                else:
                    subnet_ids = R.discover_subnets(ec2, vpc_id)

                if egress.operator_role_arn:
                    operator_role_arn = egress.operator_role_arn
                    emit(f"Egress connector: reusing operator role {operator_role_arn}")
                    state.set_resource(
                        "network_operator_role", "iam-role",
                        f"{egress.name}-operator", managed=False,
                        attrs={"arn": operator_role_arn},
                    )
                else:
                    op_name = f"{egress.name}-operator"
                    emit(f"Egress connector: creating operator role '{op_name}'")
                    op_role = R.ensure_network_connector_operator_role(
                        iam, name=op_name, account_id=self._account_id()
                    )
                    operator_role_arn = op_role["arn"]
                    state.set_resource(
                        "network_operator_role", "iam-role", op_role["name"],
                        managed=True, attrs={"arn": operator_role_arn},
                    )
                self._store.save(state)

                emit(
                    f"Egress connector: creating VPC_EGRESS connector '{egress.name}' "
                    "(provisioning ENIs can take several minutes)..."
                )
                core = self._client("lambda-core")
                connector = R.ensure_network_connector(
                    core,
                    name=egress.name,
                    subnet_ids=subnet_ids,
                    security_group_ids=[sg_id],
                    operator_role_arn=operator_role_arn,
                )
                R.wait_network_connector_active(core, connector["arn"])
                emit(f"Egress connector: active {connector['arn']}")
                state.set_resource(
                    "network_connector", "network-connector", connector["arn"],
                    managed=True,
                )
                outputs["egress_network_connector_arn"] = connector["arn"]
                outputs["security_group_id"] = sg_id
                outputs["subnet_ids"] = subnet_ids
                outputs["vpc_id"] = vpc_id

        state.outputs = outputs
        self._store.save(state)
        emit(f"State saved to {self._store.path}")
        return outputs

    def _bucket_name(self, state: StackState) -> str:
        """Reuse the previously-created bucket name, else let AWS generate one."""
        existing = state.get_resource("bucket")
        if existing and existing.managed:
            return existing.id
        # Deterministic-ish name; S3 bucket names are globally unique so include
        # project/stack. Users can pin their own via bucket.name in sandbox.yaml.
        import uuid

        return f"{self._cfg.project}-{self._cfg.stack}-{uuid.uuid4().hex[:8]}"

    # -- plan (preview) ----------------------------------------------------

    def plan(self) -> list[dict[str, str]]:
        """Best-effort dry run: report create/reuse per resource."""
        cfg = self._cfg
        actions: list[dict[str, str]] = []

        actions.append(
            {"resource": "iam-role", "name": cfg.role.name,
             "action": "reuse" if cfg.role.reuse else "create"}
        )
        actions.append(
            {"resource": "s3-bucket", "name": cfg.bucket.name or "(generated)",
             "action": "reuse" if cfg.bucket.reuse else "create"}
        )
        actions.append(
            {"resource": "s3-object", "name": f"microvm-images/{cfg.image.name}.zip",
             "action": "upload"}
        )

        mv = self._client("lambda-microvms")
        image_action = "reuse" if R._image_active_version(mv, cfg.image.name) else "create"
        actions.append(
            {"resource": "microvm-image", "name": cfg.image.name, "action": image_action}
        )

        if cfg.network.enabled:
            egress = cfg.network.egress
            if egress.reuse:
                actions.append(
                    {"resource": "network-connector", "name": egress.connector_arn,
                     "action": "reuse"}
                )
            else:
                actions.append(
                    {"resource": "iam-role",
                     "name": egress.operator_role_arn or f"{egress.name}-operator",
                     "action": "reuse" if egress.operator_role_arn else "create"}
                )
                actions.append(
                    {"resource": "ec2-sg",
                     "name": egress.security_group_id or "(create)",
                     "action": "reuse" if egress.reuse_sg else "create"}
                )
                actions.append(
                    {"resource": "network-connector", "name": egress.name,
                     "action": "create"}
                )
        return actions

    # -- destroy -----------------------------------------------------------

    def destroy(self, *, report: Reporter | None = None) -> None:
        emit = report or _noop
        cfg = self._cfg
        state = self._store.load(cfg.project, cfg.stack)

        # Reverse dependency order; only tear down resources we manage.
        # The connector holds ENIs in the SG, so it must go before the SG.
        connector = state.get_resource("network_connector")
        if connector and connector.managed:
            emit(f"Deleting network connector {connector.id}")
            core = self._client("lambda-core")
            R.delete_network_connector(core, connector.id)
            try:
                R.wait_network_connector_deleted(core, connector.id)
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        elif connector:
            emit(f"Leaving reused network connector {connector.id}")

        sg = state.get_resource("security_group")
        if sg and sg.managed:
            emit(f"Deleting security group {sg.id}")
            R.delete_security_group(self._client("ec2"), sg.id)
        elif sg:
            emit(f"Leaving reused security group {sg.id}")

        operator_role = state.get_resource("network_operator_role")
        if operator_role and operator_role.managed:
            emit(f"Deleting network connector operator role '{operator_role.id}'")
            R.delete_role(self._client("iam"), operator_role.id)
        elif operator_role:
            emit(f"Leaving reused operator role '{operator_role.id}'")

        image = state.get_resource("image")
        if image and image.managed:
            emit(f"Deleting MicroVM image {image.id}")
            R.delete_image(self._client("lambda-microvms"), image.id)
        elif image:
            emit(f"Leaving reused MicroVM image {image.id}")

        bucket = state.get_resource("bucket")
        if bucket and bucket.managed:
            emit(f"Emptying and deleting S3 bucket '{bucket.id}'")
            # Deleting the bucket also removes the guest object.
            R.delete_bucket(self._client("s3"), bucket.id)
        elif bucket:
            emit(f"Leaving reused S3 bucket '{bucket.id}'")

        role = state.get_resource("role")
        if role and role.managed:
            emit(f"Deleting IAM role '{role.id}'")
            R.delete_role(self._client("iam"), role.id)
        elif role:
            emit(f"Leaving reused IAM role '{role.id}'")

        self._store.clear(cfg.project, cfg.stack)
        emit("State cleared")

    # -- refresh -----------------------------------------------------------

    def refresh(self) -> dict[str, Any]:
        """Re-read live resource state; prune vanished resources from state."""
        cfg = self._cfg
        state = self._store.load(cfg.project, cfg.stack)
        mv = self._client("lambda-microvms")

        image = state.get_resource("image")
        if image:
            try:
                gi = mv.get_microvm_image(imageIdentifier=image.id)
                image.attrs["version"] = gi.get("latestActiveImageVersion")
            except Exception:  # noqa: BLE001 - resource may be gone
                state.pop_resource("image")
                state.outputs.pop("image_arn", None)

        self._store.save(state)
        return state.outputs

    # -- outputs -----------------------------------------------------------

    def outputs(self) -> dict[str, Any]:
        return self._store.load(self._cfg.project, self._cfg.stack).outputs


def read_outputs(cfg: InfraConfig) -> dict[str, Any]:
    """Best-effort read of persisted outputs for CLI auto-wiring.

    Never raises: returns an empty dict if state is unavailable so callers can
    fall back to env vars / explicit flags.
    """
    try:
        return InfraStateStore().load(cfg.project, cfg.stack).outputs
    except Exception:  # noqa: BLE001 - auto-wire must never hard-fail
        return {}
