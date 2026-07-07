"""End-to-end example mirroring microsandbox's README.

Requires the infra to be deployed (see README). Provide the image ARN and
execution role ARN via env vars:

    export AGENT_SANDBOX_IMAGE_ARN=$(asb infra output image_arn)
    export AGENT_SANDBOX_EXECUTION_ROLE_ARN=$(asb infra output execution_role_arn)
    python examples/run_code.py
"""

import asyncio

from agent_sandbox import Sandbox


async def main() -> None:
    sandbox = await Sandbox.create(
        "my-sandbox",
        cpus=1,
        memory=512,
    )
    try:
        output = await sandbox.exec("python", ["-c", "print('Hello from a microVM!')"])
        print(output.stdout_text, end="")
    finally:
        await sandbox.stop()


if __name__ == "__main__":
    asyncio.run(main())
