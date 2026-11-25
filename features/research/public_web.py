"""Small, bounded public-page reader for checking research evidence (no cookies).
Adapted from Morrow (MIT, (c) 2026 Morrow contributors): common/public_web.py. See features/learning/LICENSE-morrow."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from html.parser import HTMLParser


class _Page(HTMLParser):
    """Visible text and link targets of a page (stands in for BeautifulSoup in Morrow's version)."""
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


def public_url(value):
    try:
        url = urlsplit(str(value))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            return False
        if url.port not in (None, 80, 443) or any(c in str(value) for c in ('\r', '\n', '\x00')):
            return False
        host = url.hostname.lower().rstrip('.')
        if host == 'localhost' or host.endswith(('.local', '.localhost', '.internal')) or '.' not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def fetch_page(url):
    for _ in range(4):
        if not public_url(url):
            raise ValueError('Only public HTTP(S) source pages can be checked.')
        part = urlsplit(url)
        port = part.port or (443 if part.scheme == 'https' else 80)
        addresses = {record[4][0] for record in socket.getaddrinfo(part.hostname, port, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError('The source resolves to a private address.')
        # Pin the checked address to prevent a second DNS lookup or proxy from reaching a local service.
        connection = http.client.HTTPConnection(part.hostname, port, timeout=8)
        connection.sock = socket.create_connection((sorted(addresses)[0], port), timeout=8)
        if part.scheme == 'https':
            connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=part.hostname)
        try:
            connection.request('GET', (part.path or '/') + ('?' + part.query if part.query else ''),
                               headers={'User-Agent': 'ReachoutCompanyResearch/1.0 (+evidence check)', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise ValueError('Source returned HTTP %s.' % response.status)
            content_type = response.getheader('Content-Type', '').lower()
            if not any(t in content_type for t in ('text/', 'application/json', 'application/ld+json', 'xhtml')):
                raise ValueError('Source is not a readable web page.')
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('Source exceeds the page size limit.')
            page = _Page()
            page.feed(raw.decode(response.headers.get_content_charset() or 'utf-8', 'replace'))
            return {'url': url, 'text': ' '.join(page.text), 'links': [urljoin(url, h) for h in page.links]}
        finally:
            connection.close()
    raise ValueError('Source redirected too many times.')
