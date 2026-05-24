"""Test suite for CodeCortex PHASE 1 core modules."""

import pytest
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import (
    NodeKind,
    EdgeKind,
    NodeInfo,
    EdgeInfo,
    SourceRange,
    LanguageParser,
    ParserRegistry,
    CodeCortexContext,
    LanguageNotSupportedException,
)
from indexing import LanguageConfig, LanguageConfigRegistry
from traversal import TraversalEngine, TraversalNode


class TestCoreTypes:
    """Test core data types."""
    
    def test_node_info_creation(self):
        """Test NodeInfo creation and qualified name generation."""
        node = NodeInfo(
            kind=NodeKind.FUNCTION,
            name="my_func",
            file_path="module.py",
            range=SourceRange(10, 20),
            parent_name="MyClass",
        )
        
        assert node.name == "my_func"
        assert node.qualified_name == "MyClass.my_func"
        assert node.kind == NodeKind.FUNCTION
    
    def test_edge_info_creation(self):
        """Test EdgeInfo creation."""
        edge = EdgeInfo(
            kind=EdgeKind.CALLS,
            source_qualified="MyClass.method1",
            target_qualified="MyClass.method2",
            file_path="module.py",
        )
        
        assert edge.kind == EdgeKind.CALLS
        assert edge.source_qualified == "MyClass.method1"
        assert edge.target_qualified == "MyClass.method2"


class TestParserFramework:
    """Test language parser framework."""
    
    def test_parser_registry_creation(self):
        """Test parser registry initialization."""
        registry = ParserRegistry()
        assert len(registry.supported_languages()) == 0
    
    def test_register_parser(self):
        """Test registering a language parser."""
        class DummyParser(LanguageParser):
            language = "dummy"
            extensions = [".dummy"]
            
            def parse(self, source, file_path):
                pass
            
            def extract_definitions(self, tree, source):
                return []
            
            def extract_calls(self, tree, source):
                return []
            
            def extract_imports(self, tree, source):
                return []
            
            def extract_inheritance(self, tree, source):
                return []
        
        registry = ParserRegistry()
        registry.register(DummyParser)
        
        assert "dummy" in registry.supported_languages()
        assert registry.get_parser("dummy") is not None
        assert ".dummy" in registry.supported_extensions()


class TestLanguageConfig:
    """Test language configuration system."""
    
    def test_language_config_creation(self):
        """Test LanguageConfig creation."""
        config_dict = {
            "extensions": [".py"],
            "node_kinds": {
                "function": "function_definition",
                "class": "class_definition",
            },
            "handlers": ["PythonParser"],
        }
        
        config = LanguageConfig("python", config_dict)
        assert config.language == "python"
        assert config.supports_extension(".py")
        assert config.get_node_kind("function") == "function_definition"
    
    def test_language_config_registry(self):
        """Test language config registry."""
        registry = LanguageConfigRegistry()
        
        # Check default languages loaded
        langs = registry.supported_languages()
        assert "python" in langs
        assert "javascript" in langs
        assert "typescript" in langs


class TestCodeCortexContext:
    """Test resource lifecycle management."""
    
    def test_context_creation_and_cleanup(self):
        """Test context manager lifecycle."""
        with CodeCortexContext() as ctx:
            cache = ctx.get_parser_cache()
            assert cache is not None
            
            # Register a test resource
            ctx.register_resource("test", "test_value")
            assert ctx.get_resource("test") == "test_value"
    
    def test_context_cleanup_on_exit(self):
        """Test context cleanup on exit."""
        ctx = CodeCortexContext()
        cache = ctx.get_parser_cache()
        
        cache.set("key", "value")
        assert cache.get("key") == "value"
        
        ctx.cleanup()
        # After cleanup, resource should not be accessible
        assert ctx._closed


class TestTraversalEngine:
    """Test unified graph traversal engine."""
    
    def test_traversal_node_creation(self):
        """Test TraversalNode creation."""
        node = TraversalNode(
            qualified_name="Class.method",
            node_kind=NodeKind.FUNCTION,
            depth=2,
            path=["Root", "Intermediate", "Class.method"],
            edges_traversed=[EdgeKind.CALLS, EdgeKind.CALLS],
        )
        
        assert node.qualified_name == "Class.method"
        assert node.depth == 2
        assert len(node.path) == 3


class TestTTLCache:
    """Test TTL cache implementation."""
    
    def test_cache_set_get(self):
        """Test cache set/get operations."""
        from core import TTLCache
        
        cache = TTLCache(max_size=10, ttl_seconds=3600)
        
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        
        cache.set("key2", {"nested": "data"})
        assert cache.get("key2") == {"nested": "data"}
    
    def test_cache_clear(self):
        """Test cache clear."""
        from core import TTLCache
        
        cache = TTLCache()
        cache.set("key", "value")
        cache.clear()
        assert cache.get("key") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
