"""Resource lifecycle management for CodeCortex.

Provides context manager for managing parser caches, embeddings, graph stores,
and other resources with automatic cleanup.
"""

from __future__ import annotations

import atexit
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple thread-safe time-to-live cache."""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """Initialize TTL cache.
        
        Args:
            max_size: Maximum entries before LRU eviction
            ttl_seconds: Time-to-live for entries
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = Lock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired.
        
        Args:
            key: Cache key
        
        Returns:
            Cached value or None if expired/missing
        """
        import time
        with self._lock:
            if key not in self._cache:
                return None
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[key]
                return None
            return value
    
    def set(self, key: str, value: Any) -> None:
        """Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        import time
        with self._lock:
            if len(self._cache) >= self.max_size:
                # Simple LRU: remove first (oldest) entry
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[key] = (value, time.time())
    
    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()


class CodeCortexContext:
    """Lifecycle-managed context for CodeCortex resources.
    
    Manages:
    - Parser caches
    - Embedding provider instances
    - Graph store connections
    - Thread pools
    
    Use as context manager:
        with CodeCortexContext() as ctx:
            parser = ctx.get_parser("python")
            store = ctx.get_graph_store()
    """
    
    # Class-level lock for thread safety
    _instances_lock = Lock()
    _active_instances: set[CodeCortexContext] = set()
    
    def __init__(self, config: Optional[dict[str, Any]] = None):
        """Initialize CodeCortex context.
        
        Args:
            config: Optional configuration dict with keys:
                - parser_cache_size: int (default 1000)
                - parser_cache_ttl: int seconds (default 3600)
                - embeddings: dict with provider config
                - graph_store_path: str or Path
        """
        self.config = config or {}
        self._resources: dict[str, Any] = {}
        self._lock = Lock()
        self._closed = False
        
        # Register cleanup on exit
        with self._instances_lock:
            self._active_instances.add(self)
    
    def __enter__(self) -> CodeCortexContext:
        """Enter context manager."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager and cleanup resources."""
        self.cleanup()
    
    def get_parser_cache(self) -> TTLCache:
        """Get or create parser cache.
        
        Returns:
            TTLCache instance
        """
        with self._lock:
            if "parser_cache" not in self._resources:
                cache_size = self.config.get("parser_cache_size", 1000)
                cache_ttl = self.config.get("parser_cache_ttl", 3600)
                self._resources["parser_cache"] = TTLCache(cache_size, cache_ttl)
            return self._resources["parser_cache"]
    
    def get_resource(self, name: str) -> Optional[Any]:
        """Get a registered resource.
        
        Args:
            name: Resource name
        
        Returns:
            Resource or None if not found
        """
        with self._lock:
            return self._resources.get(name)
    
    def register_resource(self, name: str, resource: Any) -> None:
        """Register a resource for cleanup.
        
        Args:
            name: Resource name
            resource: Resource object
        """
        with self._lock:
            self._resources[name] = resource
    
    def cleanup(self) -> None:
        """Clean up all resources.
        
        Closes connections, clears caches, releases thread pools, etc.
        """
        if self._closed:
            return
        
        with self._lock:
            logger.debug(f"Cleaning up {len(self._resources)} resources")
            
            for name, resource in list(self._resources.items()):
                try:
                    if hasattr(resource, "close"):
                        resource.close()
                        logger.debug(f"Closed resource: {name}")
                    elif hasattr(resource, "shutdown"):
                        resource.shutdown()
                        logger.debug(f"Shut down resource: {name}")
                    elif hasattr(resource, "clear"):
                        resource.clear()
                        logger.debug(f"Cleared resource: {name}")
                except Exception as e:
                    logger.warning(f"Error cleaning up {name}: {e}")
            
            self._resources.clear()
            self._closed = True
        
        # Unregister from active instances
        with self._instances_lock:
            self._active_instances.discard(self)
    
    @classmethod
    def cleanup_all(cls) -> None:
        """Clean up all active context instances."""
        with cls._instances_lock:
            for ctx in list(cls._active_instances):
                ctx.cleanup()


def cleanup_all() -> None:
    """Clean up all active CodeCortexContext instances."""
    CodeCortexContext.cleanup_all()


# Global context (optional)
_default_context: Optional[CodeCortexContext] = None
_default_context_lock = Lock()


def get_default_context(create: bool = False) -> Optional[CodeCortexContext]:
    """Get the default global CodeCortex context.
    
    Args:
        create: If True, create context if it doesn't exist
    
    Returns:
        CodeCortexContext or None
    """
    global _default_context
    
    if _default_context is None and create:
        with _default_context_lock:
            if _default_context is None:
                _default_context = CodeCortexContext()
                atexit.register(lambda: _default_context.cleanup())
    
    return _default_context


def set_default_context(ctx: CodeCortexContext) -> None:
    """Set the default global CodeCortex context.
    
    Args:
        ctx: CodeCortexContext instance
    """
    global _default_context
    _default_context = ctx


@contextmanager
def create_context(config: Optional[dict[str, Any]] = None) -> Generator[CodeCortexContext, None, None]:
    """Context manager factory for CodeCortex context.
    
    Args:
        config: Optional configuration dict
    
    Yields:
        CodeCortexContext instance
    """
    ctx = CodeCortexContext(config)
    try:
        yield ctx
    finally:
        ctx.cleanup()
