"""Minimal JSON-RPC lifecycle shared by the verified stdio adapter."""
import json
import subprocess
from .schema import require

class Session:
    def __enter__(self):
        try:
            self.call("initialize", {"clientInfo": {"name": "mml_C1", "version": "1"},
                                    "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
            return self
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        seq = self.sequence
        self.send({"id": seq, "method": method, "params": params})
        while True:
            value = self.receive()
            if value.get("id") == seq:
                require("result" in value and "error" not in value, "Codex_RPC_rejected:" + method)
                return value["result"]
            require("id" not in value, "unexpected_Codex_RPC_response")
            self.events.append(value)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()
        self.selector.close()
        self.temp.cleanup()

    def __exit__(self, *args):
        self.close()
