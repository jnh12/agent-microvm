"""agentd: the in-VM guest agent.

Runs inside the Lambda MicroVM image and exposes a small HTTP API that the SDK's
``AgentClient`` calls to execute commands and read/write files. This is the
Python analogue of microsandbox's ``agentd`` guest agent.
"""

__version__ = "0.1.0"
