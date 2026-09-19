"""MCP Apps (SEP-1865) client-side support: capability advertisement and
model-facing visibility filtering.

The Python MCP SDK has no first-class ``capabilities.extensions`` hook yet
(ClientCapabilities carries it only as an extra field), so ``initialize`` is
wrapped once — same approach as template-agent; revisit on SDK upgrades.

Model-facing filtering: App servers may mark tools with
``_meta.ui.visibility``; tools visible only to the app UI are excluded from
the model's tool list (they exist for the host proxy, not for the LLM).
"""

from typing import Any

from langchain_core.tools import BaseTool
from mcp import types
from mcp.client.session import ClientSession

MCP_APPS_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_APPS_MIME_TYPE = "text/html;profile=mcp-app"

_original_initialize: Any = None
_patch_installed = False


def _extension_settings() -> dict[str, Any]:
    """The settings map advertised under capabilities.extensions."""
    return {"mimeTypes": [MCP_APPS_MIME_TYPE]}


def inject_ui_extension_into_request(request: types.ClientRequest) -> types.ClientRequest:
    """Return *request* with the MCP Apps capability on initialize (idempotent;
    non-initialize requests pass through unchanged)."""
    root = request.root
    if not isinstance(root, types.InitializeRequest):
        return request
    caps = root.params.capabilities
    existing = getattr(caps, "extensions", None) or {}
    if MCP_APPS_EXTENSION_ID in existing:
        return request
    extensions = {**existing, MCP_APPS_EXTENSION_ID: _extension_settings()}
    new_caps = caps.model_copy(update={"extensions": extensions})
    new_params = root.params.model_copy(update={"capabilities": new_caps})
    return types.ClientRequest(root.model_copy(update={"params": new_params}))


async def _initialize_with_mcp_apps(self: ClientSession) -> types.InitializeResult:
    """Wrap stock initialize so the handshake advertises MCP Apps support."""
    if _original_initialize is None:
        raise RuntimeError("MCP Apps initialize patch was not installed")
    original_send_request = self.send_request

    async def send_request_with_extensions(request: Any, result_type: Any, **kwargs: Any) -> Any:
        if isinstance(request, types.ClientRequest):
            request = inject_ui_extension_into_request(request)
        return await original_send_request(request, result_type, **kwargs)

    # Deliberate monkeypatch of the SDK session — the only way to inject
    # capabilities.extensions today (see module docstring).
    self.send_request = send_request_with_extensions  # ty: ignore[invalid-assignment]
    try:
        return await _original_initialize(self)
    finally:
        self.send_request = original_send_request  # ty: ignore[invalid-assignment]


def ensure_mcp_apps_capability_advertised() -> bool:
    """Patch ``ClientSession.initialize`` once. Returns True when newly installed."""
    global _original_initialize, _patch_installed
    if _patch_installed:
        return False
    _original_initialize = ClientSession.initialize
    ClientSession.initialize = _initialize_with_mcp_apps
    _patch_installed = True
    return True


def get_tool_ui_meta(tool: BaseTool) -> dict[str, Any]:
    """The tool's MCP Apps UI metadata (``_meta.ui``, legacy flat form tolerated)."""
    meta = (tool.metadata or {}).get("_meta") or {}
    ui = meta.get("ui")
    if isinstance(ui, dict):
        return ui
    legacy = meta.get("ui/resourceUri")
    return {"resourceUri": legacy} if legacy else {}


def is_model_facing(tool: BaseTool) -> bool:
    """Whether the tool belongs in the model's list (visibility default: model+app)."""
    visibility = get_tool_ui_meta(tool).get("visibility")
    if not isinstance(visibility, list):
        return True
    return "model" in visibility


def filter_model_facing_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Drop app-only tools from the model-facing list (they remain reachable
    through the host proxy)."""
    return [t for t in tools if is_model_facing(t)]
