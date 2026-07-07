"""Egress-only security group for MicroVMs.

MicroVMs initiate outbound connections (package installs, API calls); they never
need inbound rules, so the group has no ingress and open egress. Created only
when the user opts into VPC attachment and does not supply an existing SG.
"""

from __future__ import annotations

import pulumi
import pulumi_aws as aws


class EgressSecurityGroup(pulumi.ComponentResource):
    security_group: aws.ec2.SecurityGroup
    security_group_id: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("agent-sandbox:infra:EgressSecurityGroup", name, None, opts)

        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            description="Egress-only SG for agent-sandbox-os MicroVMs.",
            vpc_id=vpc_id,
            ingress=[],
            egress=[
                {
                    "protocol": "-1",
                    "from_port": 0,
                    "to_port": 0,
                    "cidr_blocks": ["0.0.0.0/0"],
                    "description": "Allow all outbound.",
                }
            ],
            tags={"Name": f"{name}-egress"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.security_group_id = self.security_group.id
        self.register_outputs({"security_group_id": self.security_group_id})
