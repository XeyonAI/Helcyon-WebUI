"""Small ComfyUI HTTP client used by HWUI's image-generation bridge."""

from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


class ComfyUIError(RuntimeError):
    pass


def build_base_url(host: str, port: int | str) -> str:
    host = str(host or "127.0.0.1").strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    try:
        port_number = int(port)
    except (TypeError, ValueError) as error:
        raise ComfyUIError("ComfyUI port must be a number.") from error
    if not 1 <= port_number <= 65535:
        raise ComfyUIError("ComfyUI port must be between 1 and 65535.")
    return f"{host}:{port_number}"


def load_workflow_template(path: str, prompt_node_id: str, output_node_id: str) -> dict[str, Any]:
    workflow_path = Path(str(path or "").strip())
    if not workflow_path.is_file():
        raise ComfyUIError(f"ComfyUI workflow file not found: {workflow_path}")
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComfyUIError(f"Could not read ComfyUI workflow JSON: {error}") from error
    if not isinstance(workflow, dict):
        raise ComfyUIError("ComfyUI workflow JSON must contain an API-format object.")
    for label, node_id in (("prompt", prompt_node_id), ("output", output_node_id)):
        node = workflow.get(str(node_id))
        if not isinstance(node, dict):
            raise ComfyUIError(f"Configured ComfyUI {label} node ID was not found: {node_id}")
    prompt_inputs = workflow[str(prompt_node_id)].get("inputs")
    if not isinstance(prompt_inputs, dict) or "text" not in prompt_inputs:
        raise ComfyUIError(
            f"ComfyUI prompt node {prompt_node_id} has no inputs.text field. "
            "Export the workflow in API format and select its positive prompt node."
        )
    return workflow


def inject_prompt(workflow: dict[str, Any], prompt_node_id: str, image_prompt: str) -> dict[str, Any]:
    prepared = copy.deepcopy(workflow)
    prepared[str(prompt_node_id)]["inputs"]["text"] = image_prompt
    return prepared


def prune_workflow_to_output(
    workflow: dict[str, Any], output_node_id: str, *, required_node_ids: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Copy only the selected output node and its recursive upstream dependencies."""
    output_node_id = str(output_node_id)
    if output_node_id not in workflow:
        raise ComfyUIError(f"Configured ComfyUI output node ID was not found: {output_node_id}")

    retained: set[str] = set()
    pending = [output_node_id]
    while pending:
        node_id = pending.pop()
        if node_id in retained:
            continue
        retained.add(node_id)

        def collect_links(value: Any) -> None:
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in workflow
            ):
                pending.append(value[0])
            elif isinstance(value, dict):
                for nested in value.values():
                    collect_links(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_links(nested)

        node = workflow.get(node_id) or {}
        collect_links(node.get("inputs", {}))

    missing_required = [str(node_id) for node_id in required_node_ids if str(node_id) not in retained]
    if missing_required:
        raise ComfyUIError(
            "Configured ComfyUI prompt node is not upstream of the selected output node: "
            + ", ".join(missing_required)
        )
    return {node_id: copy.deepcopy(workflow[node_id]) for node_id in workflow if node_id in retained}


class ComfyUIClient:
    def __init__(self, base_url: str, *, session=None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def preflight(self) -> None:
        try:
            response = self.session.get(f"{self.base_url}/system_stats", timeout=(3, 8))
            response.raise_for_status()
        except requests.RequestException as error:
            raise ComfyUIError(f"ComfyUI is unavailable at {self.base_url}: {error}") from error

    def queue(self, workflow: dict[str, Any]) -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": str(uuid.uuid4())},
                timeout=(5, 30),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ComfyUIError(f"ComfyUI rejected the workflow: {error}") from error
        prompt_id = str(payload.get("prompt_id", "")).strip()
        if not prompt_id:
            detail = payload.get("error") or payload.get("node_errors") or "no prompt_id returned"
            raise ComfyUIError(f"ComfyUI rejected the workflow: {detail}")
        return prompt_id

    def wait_for_images(self, prompt_id: str, output_node_id: str, *, timeout: int = 900) -> list[dict[str, str]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = self.session.get(
                    f"{self.base_url}/history/{quote(prompt_id, safe='')}", timeout=(5, 20)
                )
                response.raise_for_status()
                history = response.json()
            except (requests.RequestException, ValueError) as error:
                raise ComfyUIError(f"Lost contact with ComfyUI while waiting: {error}") from error
            job = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(job, dict):
                status = job.get("status") or {}
                if status.get("status_str") == "error":
                    messages = status.get("messages") or []
                    raise ComfyUIError(f"ComfyUI generation failed: {messages or 'unknown execution error'}")
                output = (job.get("outputs") or {}).get(str(output_node_id)) or {}
                images = output.get("images") or []
                if images:
                    return images
                if status.get("completed"):
                    raise ComfyUIError(
                        f"ComfyUI completed, but output node {output_node_id} returned no images."
                    )
            time.sleep(1)
        raise ComfyUIError(f"ComfyUI generation timed out after {timeout} seconds.")

    def fetch_image(self, image: dict[str, str]) -> tuple[bytes, str]:
        params = {
            "filename": image.get("filename", ""),
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
        if not params["filename"]:
            raise ComfyUIError("ComfyUI returned an image without a filename.")
        try:
            response = self.session.get(f"{self.base_url}/view", params=params, timeout=(5, 120))
            response.raise_for_status()
        except requests.RequestException as error:
            raise ComfyUIError(f"Could not retrieve the generated image: {error}") from error
        content_type = response.headers.get("Content-Type", "image/png").split(";", 1)[0]
        if not content_type.startswith("image/") or not response.content:
            raise ComfyUIError("ComfyUI returned an invalid image response.")
        return response.content, content_type

    def release_vram(self) -> None:
        try:
            response = self.session.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=(5, 60),
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise ComfyUIError(
                "ComfyUI finished, but did not confirm that its models were unloaded; "
                f"Helcyon was not restored to avoid a VRAM collision. {error}"
            ) from error
