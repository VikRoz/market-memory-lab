"""Offline installed-SDK contract check. Connection discovery/network are explicitly stubbed."""

import inspect
import importlib
import json
from importlib import metadata
from unittest.mock import patch


PINNED = {"falkordb": "1.8.0", "redis": "8.0.0", "python-dateutil": "2.9.0.post0", "six": "1.17.0"}
CONSTRUCTOR_OPTIONS = {"host", "port", "username", "password", "ssl", "socket_connect_timeout",
                       "socket_timeout", "retry", "ssl_check_hostname", "cluster_error_retry_attempts"}


def check():
    versions = {}
    for name, expected in PINNED.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            return {"status": "not_installed", "package": name, "network_used": False}
        versions[name] = actual
        if actual != expected:
            return {"status": "version_mismatch", "package": name, "expected": expected, "actual": actual,
                    "network_used": False}
    from falkordb import FalkorDB
    from falkordb.graph import Graph
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    if not CONSTRUCTOR_OPTIONS <= set(inspect.signature(FalkorDB).parameters):
        return {"status": "constructor_mismatch", "network_used": False}
    for method in (Graph.query, Graph.ro_query):
        inspect.signature(method).bind(None, "RETURN 1", params={}, timeout=2000)
    inspect.signature(FalkorDB.close).bind(None)
    module = importlib.import_module("falkordb.falkordb")
    commands = []
    def fake_wire(*command):
        if command[0] not in ("GRAPH.QUERY", "GRAPH.RO_QUERY"):
            raise AssertionError("Unexpected offline driver command")
        commands.append(command)
        return [[[1, "payload"], [1, "number"], [1, "properties"], [1, "labels"], [1, "edge_properties"]],
                [[[2, "synthetic"], [3, 1], [10, ["epoch", [3, 1], "status", [2, "pending"]]],
                  [6, [[2, "IngestionBatch"]]], [10, []]]],
                ["Query internal execution time: 0.1 milliseconds"]]
    with patch.object(module, "Is_Sentinel", return_value=False), patch.object(module, "Is_Cluster", return_value=False), \
            patch("socket.socket.connect", side_effect=AssertionError("Offline probe forbids network")), \
            patch("socket.getaddrinfo", side_effect=AssertionError("Offline probe forbids DNS")):
        client = FalkorDB(host="offline.invalid", port=6379, username=None, password=None, ssl=True,
                         socket_connect_timeout=2, socket_timeout=3, retry=Retry(NoBackoff(), 0),
                         ssl_check_hostname=True, cluster_error_retry_attempts=0)
        try:
            client.execute_command = fake_wire
            graph = client.select_graph("mml_offline_contract")
            if graph.name != "mml_offline_contract":
                return {"status": "graph_identity_mismatch", "network_used": False}
            for method in (graph.ro_query, graph.query):
                result = method("RETURN $value, 1, {epoch: 1, status: 'pending'}, ['IngestionBatch'], {}", params={"value": "synthetic"}, timeout=2000)
                if result.result_set != [["synthetic", 1, {"epoch": 1, "status": "pending"}, ["IngestionBatch"], {}]]:
                    return {"status": "result_shape_mismatch", "network_used": False}
            if [cmd[0] for cmd in commands] != ["GRAPH.RO_QUERY", "GRAPH.QUERY"]:
                return {"status": "driver_command_mismatch", "network_used": False}
            if any("timeout" not in cmd or cmd[-1] != 2000 for cmd in commands):
                return {"status": "driver_timeout_mismatch", "network_used": False}
        finally:
            client.close()
    return {"status": "offline_contract_ok", "versions": versions, "network_used": False,
            "constructor_called": True, "network_negotiation_stubbed": True, "live_database_verified": False}


def main():
    try:
        result = check()
    except Exception:
        result = {"status": "offline_contract_failed", "network_used": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "offline_contract_ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
