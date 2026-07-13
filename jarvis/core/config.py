"""
JARVIS Configuration System.

Centralized configuration with environment variable override,
file-based presets, and runtime hot-reload capability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseSettings):
    """LLM model configuration."""

    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 1.0

    # Model routing: which model for which task
    task_model_map: dict[str, str] = {
        "code_generation": "gpt-4o",
        "research": "gpt-4o-mini",
        "creative_writing": "claude-3-5-sonnet",
        "translation": "gpt-4o-mini",
        "vision": "gpt-4o",
        "embedding": "text-embedding-3-small",
    }


class MemoryConfig(BaseSettings):
    """Vector memory configuration."""

    engine: str = "chromadb"
    persist_dir: str = "./data/memory"
    embedding_model: str = "text-embedding-3-small"
    max_context_length: int = 100000
    retrieval_top_k: int = 20
    auto_compress_threshold: int = 1000  # conversations after which to compress


class SandboxConfig(BaseSettings):
    """Code execution sandbox configuration."""

    engine: str = "docker"  # docker / podman / local
    image: str = "jarvis-sandbox:latest"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    timeout_seconds: int = 300
    network_enabled: bool = False
    allowed_paths: list[str] = []


class EvolutionConfig(BaseSettings):
    """Self-evolution engine configuration."""

    enabled: bool = True
    prompt_optimization: bool = True  # Auto-optimize prompts via TextGrad
    model_selection: bool = True      # Auto-select best model per task type
    capability_growth: bool = True    # Allow system to propose new capabilities
    feedback_collection: bool = True  # Collect outcome feedback for learning
    optimization_interval_hours: int = 24


class JARVISConfig(BaseSettings):
    """Master configuration for JARVIS."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # System identity
    name: str = "JARVIS"
    version: str = "0.1.0"
    greeting: str = "At your service, sir."

    # Paths
    data_dir: Path = Path("./data")
    log_dir: Path = Path("./logs")

    # Sub-configs
    model: ModelConfig = ModelConfig()
    memory: MemoryConfig = MemoryConfig()
    sandbox: SandboxConfig = SandboxConfig()
    evolution: EvolutionConfig = EvolutionConfig()

    # Feature flags
    domains_enabled: list[str] = [
        "personal", "research", "engineering", "creator",
        "security", "health", "finance", "home",
    ]
    web_server_enabled: bool = True
    websocket_enabled: bool = True
    voice_interface: bool = False
    multi_modal: bool = True
    scheduled_tasks: bool = True
    auto_updates: bool = False

    # Security
    require_authentication: bool = True
    allowed_users: list[str] = []
    encryption_key_path: str = ""
    audit_logging: bool = True


def load_config(config_path: Optional[Path] = None) -> JARVISConfig:
    """Load configuration from file or environment.

    Priority: explicit config_path > JARVIS_CONFIG env > project root .env > cwd .env.
    """
    if config_path and config_path.exists():
        return JARVISConfig(_env_file=str(config_path))

    # Try project root .env (the config.py's grandparent dir)
    project_root = Path(__file__).resolve().parent.parent.parent
    env_file = project_root / ".env"
    if env_file.exists():
        return JARVISConfig(_env_file=str(env_file))

    return JARVISConfig()
