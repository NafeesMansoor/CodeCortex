"""Language configuration system for CodeCortex.

Replaces hardcoded language dispatch with pluggable, config-driven language support.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LanguageConfig:
    """Configuration for a single language.

    Defines file extensions, node kinds, edge patterns, and parser hints.
    """

    def __init__(self, language: str, config_dict: dict[str, Any]):
        """Initialize language configuration.

        Args:
            language: Language name (e.g., "python")
            config_dict: Configuration dict with keys:
                - extensions: list of file extensions
                - node_kinds: dict mapping semantic names to parser node types
                - edge_patterns: dict for call/import detection
                - handlers: list of parser handler class names
        """
        self.language = language
        self.extensions: list[str] = config_dict.get("extensions", [])
        self.node_kinds: dict[str, str] = config_dict.get("node_kinds", {})
        self.edge_patterns: dict[str, Any] = config_dict.get("edge_patterns", {})
        self.handlers: list[str] = config_dict.get("handlers", [])
        self.extra: dict[str, Any] = config_dict.get("extra", {})

    def supports_extension(self, ext: str) -> bool:
        """Check if language supports a file extension.

        Args:
            ext: File extension (e.g., ".py")

        Returns:
            True if extension is supported
        """
        return ext.lower() in [e.lower() for e in self.extensions]

    def get_node_kind(self, semantic: str) -> Optional[str]:
        """Get parser node type for semantic kind.

        Args:
            semantic: Semantic name (e.g., "function")

        Returns:
            Parser node type or None
        """
        return self.node_kinds.get(semantic)


class LanguageConfigRegistry:
    """Registry for language configurations.

    Loads language configs from YAML/JSON and provides lookups.
    """

    # Language configurations for the CodeCortex target stack:
    #   Python  — covers FastAPI, Django, scripts
    #   PHP     — covers Laravel (Eloquent, controllers, artisan)
    #   JavaScript — covers Node.js, React (.js / .jsx / .mjs / .cjs)
    #   TypeScript — covers Next.js, React, Node.js (.ts / .tsx)
    DEFAULT_CONFIGS = {
        "python": {
            "extensions": [".py"],
            "node_kinds": {
                "function": "function_definition",
                "class": "class_definition",
                "module": "module",
                "variable": "assignment",
                "type": "type_hint",
                "import": "import_statement",
                "constant": "assignment",
                "test": "function_definition",
            },
            "edge_patterns": {
                "call": r"\w+\s*\(",
                "import": r"from|import",
                "class_def": r"class\s+\w+",
                "function_def": r"def\s+\w+",
            },
            "handlers": ["PythonParser"],
            "frameworks": ["FastAPI", "Django", "Flask"],
        },
        "php": {
            "extensions": [".php"],
            "node_kinds": {
                "class": "class_declaration",
                "interface": "interface_declaration",
                "trait": "trait_declaration",
                "method": "method_declaration",
                "function": "function_definition",
                "use": "use_declaration",
            },
            "edge_patterns": {
                "call": r"\w+\s*\(",
                "use": r"use\s+",
                "extends": r"extends",
                "implements": r"implements",
            },
            "handlers": ["PHPParser"],
            "frameworks": ["Laravel"],
        },
        "javascript": {
            "extensions": [".js", ".jsx", ".mjs", ".cjs"],
            "node_kinds": {
                "function": "function_declaration",
                "class": "class_declaration",
                "variable": "variable_declarator",
                "import": "import_statement",
                "export": "export_statement",
            },
            "edge_patterns": {
                "call": r"\w+\s*\(",
                "import": r"import|require",
                "class_def": r"class\s+\w+",
                "function_def": r"function\s+\w+",
            },
            "handlers": ["JavaScriptParser"],
            "frameworks": ["React", "Node.js", "Express"],
        },
        "typescript": {
            "extensions": [".ts", ".tsx"],
            "node_kinds": {
                "function": "function_declaration",
                "class": "class_declaration",
                "interface": "interface_declaration",
                "type": "type_alias_declaration",
                "variable": "variable_declarator",
                "import": "import_statement",
            },
            "edge_patterns": {
                "call": r"\w+\s*\(",
                "import": r"import|require",
                "implements": r"implements",
                "extends": r"extends",
            },
            "handlers": ["TypeScriptParser"],
            "frameworks": ["Next.js", "React", "Node.js"],
        },
    }

    def __init__(self):
        """Initialize language config registry."""
        self._configs: dict[str, LanguageConfig] = {}
        self._by_extension: dict[str, str] = {}  # ext -> language

        # Load default configs
        for language, config_dict in self.DEFAULT_CONFIGS.items():
            self.register(language, config_dict)

    def register(self, language: str, config_dict: dict[str, Any]) -> None:
        """Register a language configuration.

        Args:
            language: Language name
            config_dict: Configuration dict
        """
        config = LanguageConfig(language, config_dict)
        self._configs[language] = config

        # Index by extension
        for ext in config.extensions:
            self._by_extension[ext.lower()] = language

        logger.debug(f"Registered language config: {language}")

    def get_config(self, language: str) -> Optional[LanguageConfig]:
        """Get configuration for a language.

        Args:
            language: Language name

        Returns:
            LanguageConfig or None if not found
        """
        return self._configs.get(language)

    def get_config_by_extension(self, file_path: str) -> Optional[LanguageConfig]:
        """Get configuration by file extension.

        Args:
            file_path: Path to file

        Returns:
            LanguageConfig or None if extension not recognized
        """
        ext = Path(file_path).suffix.lower()
        if ext not in self._by_extension:
            return None
        language = self._by_extension[ext]
        return self.get_config(language)

    def load_from_file(self, config_path: str | Path) -> None:
        """Load language configs from YAML or JSON file.

        Args:
            config_path: Path to config file
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        if config_path.suffix == ".json":
            with open(config_path) as f:
                configs = json.load(f)
        else:
            try:
                import yaml

                with open(config_path) as f:
                    configs = yaml.safe_load(f)
            except ImportError:
                raise RuntimeError("PyYAML required for YAML config files")

        for language, config_dict in configs.items():
            self.register(language, config_dict)

        logger.info(f"Loaded {len(configs)} language configs from {config_path}")

    def supported_languages(self) -> list[str]:
        """Get list of supported languages.

        Returns:
            Sorted list of language names
        """
        return sorted(self._configs.keys())

    def supported_extensions(self) -> dict[str, str]:
        """Get mapping of extensions to languages.

        Returns:
            Dict mapping file extension to language name
        """
        return dict(self._by_extension)


# Global registry instance
_default_registry = LanguageConfigRegistry()


def get_language_config_registry() -> LanguageConfigRegistry:
    """Get the default global language config registry.

    Returns:
        LanguageConfigRegistry instance
    """
    return _default_registry


def get_language_config(language: str) -> Optional[LanguageConfig]:
    """Get language config from default registry.

    Args:
        language: Language name

    Returns:
        LanguageConfig or None
    """
    return _default_registry.get_config(language)
