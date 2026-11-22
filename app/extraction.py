import http.client
import ipaddress
import re
import socket
import ssl
from time import monotonic
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import ValidationError

from .schemas import website_url

MAX_BYTES = 1_000_000
TLS = ssl.create_default_context()
PAGE_HINT = re.compile(r"\b(about|services|products|solutions|pricing|industries|features)\b", re.I)
IPV6_TRANSITION_NETWORKS = tuple(map(ipaddress.ip_network, ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16")))


class WebsiteError(Exception):
    pass


class UnsafeWebsite(WebsiteError):
    pass


def public_ip(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip.teredo or any(ip in network for network in IPV6_TRANSITION_NETWORKS):
            return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not getattr(ip, "is_site_local", False)


def remaining(deadline):
    seconds = deadline - monotonic()
    if seconds <= 0:
        raise WebsiteError("Website fetch timed out")
    return min(5, seconds)


def normalize(raw):
    try:
        url = website_url(raw)
    except ValidationError:
        raise UnsafeWebsite("Invalid website URL or redirect") from None
    if url.port not in {80, 443}:
        raise UnsafeWebsite("Only standard web ports are supported")
    return str(url).split("#", 1)[0]


def fetch_page(raw, deadline, allowed_origin=None):
    current = normalize(raw)
    for _ in range(5):
        parts = urlsplit(current)
        if allowed_origin and parts[:2] != allowed_origin:
            raise WebsiteError("Related page redirected outside the website")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        hostname = parts.hostname
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        if not records or any(not public_ip(record[4][0]) for record in records):
            raise UnsafeWebsite("Website must resolve to public IP addresses")
        # Connect to a vetted address directly, keeping the original HTTP Host and TLS SNI.
        family, kind, protocol, _, address = next(
            (record for record in records if record[0] == socket.AF_INET), records[0]
        )
        with socket.socket(family, kind, protocol) as sock:
            sock.settimeout(remaining(deadline))
            sock.connect(address)
            transport = TLS.wrap_socket(sock, server_hostname=hostname) if parts.scheme == "https" else sock
            with transport:
                transport.settimeout(remaining(deadline))
                connection = http.client.HTTPConnection(parts.netloc)
                connection.sock = transport
                try:
                    path = parts.path or "/"
                    if parts.query:
                        path += "?" + parts.query
                    connection.request("GET", path, headers={
                        "User-Agent": "WebsiteAnalysis/1.0",
                        "Accept": "text/html, application/xhtml+xml",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    })
                    # Keep the transport open while reading a connection-close response.
                    with http.client.HTTPResponse(transport) as response:
                        response.begin()
                        location = response.getheader("Location")
                        if response.status in {301, 302, 303, 307, 308} and location:
                            current = resolve_link(current, location)
                            if current is None:
                                raise UnsafeWebsite("Invalid website redirect")
                            current = normalize(current)
                            continue
                        if response.status != 200:
                            raise WebsiteError(f"Website returned HTTP {response.status}")
                        content_type = response.headers.get_content_type()
                        if content_type not in {"text/html", "application/xhtml+xml"}:
                            raise WebsiteError("Website did not return HTML")
                        if response.getheader("Content-Encoding", "identity").lower() != "identity":
                            raise WebsiteError("Website returned unsupported compression")
                        chunks = bytearray()
                        while len(chunks) <= MAX_BYTES:
                            transport.settimeout(remaining(deadline))
                            chunk = response.read1(min(16_384, MAX_BYTES + 1 - len(chunks)))
                            if not chunk:
                                return current, bytes(chunks), response.headers.get_content_charset()
                            chunks.extend(chunk)
                        raise WebsiteError("Website response exceeded the size limit")
                finally:
                    connection.close()
    raise WebsiteError("Website redirected too many times")


def resolve_link(base, href):
    try:
        return urljoin(base, href).split("#", 1)[0]
    except ValueError:
        return None


def extract_page(url, deadline, allowed_origin=None):
    try:
        final_url, html, encoding = fetch_page(url, deadline, allowed_origin)
    except (OSError, http.client.HTTPException) as exc:
        raise WebsiteError("Website could not be reached") from exc
    soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    metadata = {}
    for element in soup.find_all("meta"):
        key = (element.get("name") or element.get("property") or "").lower()
        if key in {"description", "og:site_name", "og:title", "og:description"} and element.get("content"):
            metadata[key] = element["content"].strip()[:400]
    links = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if PAGE_HINT.search(anchor.get_text(" ", strip=True) + " " + href):
            link = resolve_link(final_url, href)
            if link:
                links.append(link)
    for element in soup.select("script, style, noscript, template, nav, footer, aside, svg, [hidden]"):
        element.decompose()
    body = soup.find("main") or soup.body or soup
    lines = dict.fromkeys(" ".join(line.split()) for line in body.stripped_strings)
    return {"url": final_url, "title": title[:250], "metadata": metadata,
            "text": "\n".join(lines)[:4600]}, links


def extract_website(url):
    deadline = monotonic() + 16
    first, links = extract_page(url, deadline)
    if not (first["text"] or first["metadata"]):
        raise WebsiteError("No readable content was found in the returned HTML")
    pages = [first]
    seen = {first["url"]}
    origin = urlsplit(first["url"])[:2]
    candidates = (link for link in dict.fromkeys(links) if urlsplit(link)[:2] == origin)
    for link in list(candidates)[:5]:
        if len(pages) == 3 or monotonic() >= deadline:
            break
        if link in seen:
            continue
        seen.add(link)
        try:
            page, _ = extract_page(link, deadline, origin)
        except WebsiteError:
            continue
        if urlsplit(page["url"])[:2] == origin and page["text"] and all(
            page["text"] != existing["text"] for existing in pages
        ):
            pages.append(page)
            seen.add(page["url"])
    return pages
