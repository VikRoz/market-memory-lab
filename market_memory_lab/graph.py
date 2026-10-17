"""Explicit FalkorDB connection; no legacy graph schema or fallback."""
import os
import re
from .model import ValidationError, DatabaseUnavailable

class GraphStore:
    def __init__(self, graph, client=None):
        self.graph, self.client = graph, client

    @classmethod
    def from_operator_env(cls, graph_name=None):
        # Called only for explicit graph commands / opted-in integration tests.
        host = os.environ.get("MML_FALKOR_HOST")
        name = graph_name or os.environ.get("MML_FALKOR_GRAPH")
        if not host or not name:
            raise ValidationError("Operator must explicitly set MML_FALKOR_HOST and MML_FALKOR_GRAPH")
        if not re.fullmatch(r"mml_[A-Za-z0-9_]{1,80}", name):
            raise ValidationError("Graph name must use the dedicated mml_ namespace")
        try:
            port = int(os.environ.get("MML_FALKOR_PORT", "6379"))
        except ValueError:
            raise ValidationError("Invalid database port") from None
        if not 1 <= port <= 65535:
            raise ValidationError("Invalid database port")
        tls = os.environ.get("MML_FALKOR_TLS", "1")
        if tls not in ("0", "1"):
            raise ValidationError("MML_FALKOR_TLS must be 0 or 1")
        if tls == "0" and host not in ("127.0.0.1", "localhost", "::1"):
            raise ValidationError("TLS is required for non-loopback database connections")
        try:
            from falkordb import FalkorDB
            from redis.backoff import NoBackoff
            from redis.retry import Retry
        except ImportError:
            raise DatabaseUnavailable("Graph extra is not installed; no fallback is available") from None
        try:
            client = FalkorDB(host=host, port=port, username=os.environ.get("MML_FALKOR_USER"),
                              password=os.environ.get("MML_FALKOR_PASSWORD"), ssl=(tls == "1"),
                              socket_connect_timeout=2, socket_timeout=3,
                              retry=Retry(NoBackoff(), 0), ssl_check_hostname=True,
                              cluster_error_retry_attempts=0)
            return cls(client.select_graph(name), client)
        except Exception:
            raise DatabaseUnavailable("Database connection failed; details suppressed to protect connection data") from None

    def close(self):
        if self.client is not None:
            self.client.close()
