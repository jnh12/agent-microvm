"""Least-privilege execution role assumed by MicroVMs.

The role is what the MicroVM's guest environment uses to call AWS. It grants
only CloudWatch Logs writes by default; attach additional managed policies via
``extra_policy_arns`` for any S3/Secrets/etc. the workload legitimately needs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pulumi
import pulumi_aws as aws


class ExecutionRole(pulumi.ComponentResource):
    role: aws.iam.Role
    role_arn: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        role_name: str | None = None,
        extra_policy_arns: Sequence[str] = (),
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("agent-sandbox:infra:ExecutionRole", name, None, opts)

        # Trust: the Lambda MicroVMs service assumes this role on your behalf.
        assume_role_policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        )

        self.role = aws.iam.Role(
            f"{name}-role",
            name=role_name,
            assume_role_policy=assume_role_policy,
            description="Execution role for agent-sandbox-os MicroVMs.",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # CloudWatch Logs scoped to this account's lambda-microvms log groups
        # rather than Resource: "*".
        region = aws.get_region_output().name
        account_id = aws.get_caller_identity_output().account_id
        log_arn = pulumi.Output.all(region, account_id).apply(
            lambda vals: f"arn:aws:logs:{vals[0]}:{vals[1]}:log-group:/aws/lambda-microvms/*"
        )

        aws.iam.RolePolicy(
            f"{name}-logs",
            role=self.role.id,
            policy=log_arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "MicroVMLogs",
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogGroup",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": [arn, f"{arn}:*"],
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        for idx, policy_arn in enumerate(extra_policy_arns):
            aws.iam.RolePolicyAttachment(
                f"{name}-extra-{idx}",
                role=self.role.name,
                policy_arn=policy_arn,
                opts=pulumi.ResourceOptions(parent=self),
            )

        self.role_arn = self.role.arn
        self.register_outputs({"role_arn": self.role_arn})
