"""Image tools over the ``lambda-microvms`` image APIs (list/inspect/remove/prune)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import err, ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry

_ARN_KEYS = ("imageArn", "arn", "imageIdentifier", "ImageArn")


def _image_arn(item: dict) -> str | None:
    for key in _ARN_KEYS:
        if item.get(key):
            return item[key]
    return None


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    @mcp.tool()
    @tool_handler
    async def image_list(managed: bool = False) -> dict:
        """List MicroVM images. Set `managed=true` for AWS-provided base images."""
        cp = registry.control_plane()
        images = (
            await cp.list_managed_microvm_images()
            if managed
            else await cp.list_microvm_images()
        )
        return ok({"managed": managed, "count": len(images), "images": images})

    @mcp.tool()
    @tool_handler
    async def image_inspect(image_arn: str) -> dict:
        """Inspect a MicroVM image's config/metadata by ARN."""
        cp = registry.control_plane()
        for managed in (False, True):
            images = (
                await cp.list_managed_microvm_images()
                if managed
                else await cp.list_microvm_images()
            )
            for item in images:
                if _image_arn(item) == image_arn:
                    return ok({"managed": managed, "image": item})
        return err(f"image not found: {image_arn}", code="not_found")

    @mcp.tool()
    @tool_handler
    async def image_remove(image_arn: str, confirm: bool = False) -> dict:
        """Delete a MicroVM image. Destructive: requires `confirm=true`."""
        if not confirm:
            return err(
                "destructive operation requires confirm=true", code="confirm_required"
            )
        cp = registry.control_plane()
        await cp.delete_microvm_image(image_arn)
        return ok({"image_arn": image_arn, "removed": True})

    @mcp.tool()
    @tool_handler
    async def image_prune(confirm: bool = False) -> dict:
        """Remove images not referenced by any live MicroVM. Requires `confirm=true`.

        Best-effort: cross-references each account image ARN against the image
        identifiers of currently listed MicroVMs; deletes the unreferenced ones.
        """
        if not confirm:
            return err(
                "destructive operation requires confirm=true", code="confirm_required"
            )
        cp = registry.control_plane()
        images = await cp.list_microvm_images()
        microvms = await cp.list_microvms()
        in_use: set[str] = set()
        for vm in microvms:
            for key in ("imageIdentifier", "imageArn", "imageVersionArn", "ImageArn"):
                val = vm.raw.get(key)
                if val:
                    in_use.add(val)
        removed, kept = [], []
        for item in images:
            arn = _image_arn(item)
            if not arn:
                continue
            if arn in in_use:
                kept.append(arn)
            else:
                await cp.delete_microvm_image(arn)
                removed.append(arn)
        return ok({"removed": removed, "kept": kept, "removed_count": len(removed)})
