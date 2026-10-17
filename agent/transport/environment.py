"""Only the already configured scoped proxy and existing account home are forwarded."""
import ipaddress
from urllib.parse import urlsplit
from .schema import require, child_environment

def proxy_address(source):
    value = source.get("MML_EGRESS_PROXY", "")
    url = urlsplit(value)
    require(url.scheme == "http" and url.port == 3128 and not url.username and not url.password
            and not url.query and not url.fragment and url.path == "", "fixed_egress_proxy_required")
    ip = ipaddress.IPv4Address(url.hostname)
    require(ip.is_private and not ip.is_loopback and not ip.is_link_local and not ip.is_unspecified,
            "scoped_proxy_private_IP_required")
    return value, str(ip)
def environment(source):
    url = proxy_address(source)[0]
    # No keys/DB/password/SSH/PYTHONPATH or arbitrary inherited proxy configuration.
    return {**child_environment(source), "RUST_LOG": "off", "HTTPS_PROXY": url,
            "HTTP_PROXY": url, "NO_PROXY": "127.0.0.1,localhost,::1"}
