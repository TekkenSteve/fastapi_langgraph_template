"""Hub settings (application layer).

Framework-held knobs live in agent_server's settings tree (the MCP tools
cache TTL the framework's loader needs, and the Fernet keys infra/crypto
reads); everything the *hub* itself needs lives here.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class HubSettings(BaseSettings):
    """Skill/MCP hub knobs (see docs/design/hub.md).

    MCP_USER_ALLOWED_DOMAINS is a comma-separated host allowlist for
    user-tier MCP connections; empty means unrestricted (template default —
    production deployments should set it).
    """

    model_config = SettingsConfigDict(extra="ignore")

    SKILL_IMPORT_MAX_BYTES: int = 5 * 1024 * 1024
    SKILL_IMPORT_TIMEOUT_SECS: float = 15.0
    MCP_USER_ALLOWED_DOMAINS: str = ""

    def allowed_mcp_domains(self) -> list[str]:
        """Parsed allowlist (lowercase hosts); empty list = unrestricted."""
        return [d.strip().lower() for d in self.MCP_USER_ALLOWED_DOMAINS.split(",") if d.strip()]


hub_settings = HubSettings()
