"""Browser and transport boundaries for a loopback-only, single-user app."""
import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from app.core.file_security import MAX_CLONE_BYTES, MAX_MEDIA_BYTES


def _origin(value: str, *, referer: bool = False) -> tuple[str, str, int] | None:
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            return None
        if not referer and (url.path or url.query or url.fragment):
            return None
        return url.scheme, url.hostname.lower(), url.port or (443 if url.scheme == 'https' else 80)
    except ValueError:
        return None


class LocalWebSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path = scope['path']

        async def secure_send(message):
            if message['type'] == 'http.response.start':
                response_headers = MutableHeaders(scope=message)
                response_headers['X-Content-Type-Options'] = 'nosniff'
                response_headers['X-Frame-Options'] = 'DENY'
                response_headers['Referrer-Policy'] = 'no-referrer'
                response_headers['Cross-Origin-Resource-Policy'] = 'same-origin'
                if path.startswith(('/api/', '/video/', '/static/')):
                    response_headers['Cache-Control'] = 'no-store'
                if path.startswith(('/video/', '/static/uploads/', '/static/output/')):
                    # Uploaded/generated documents must never execute on the app origin.
                    response_headers['Content-Security-Policy'] = "default-src 'none'; sandbox; frame-ancestors 'none'"
            await send(message)

        async def reject(code, detail):
            await JSONResponse({'detail': detail}, status_code=code)(scope, receive, secure_send)

        try:
            address = ipaddress.ip_address(scope.get('client', ('', 0))[0])
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            local = address.is_loopback
        except (ValueError, TypeError):
            local = False
        if not local:
            await reject(403, 'This application supports local access only')
            return
        hosts = headers.getlist('host')
        target = _origin(scope.get('scheme', 'http') + '://' + (hosts[0] if len(hosts) == 1 else ''))
        if target is None or target[1] not in ('127.0.0.1', 'localhost', '::1'):
            await reject(400, 'Invalid application host')
            return
        for name in ('origin', 'referer'):
            values = headers.getlist(name)
            if values and (len(values) != 1 or _origin(values[0], referer=name == 'referer') != target):
                await reject(403, 'Cross-origin requests are not allowed')
                return
        fetch_site = headers.get('sec-fetch-site', '')
        if fetch_site in ('cross-site', 'same-site'):
            # A normal top-level visit may open the UI; it cannot call a privileged API.
            navigation = path == '/' and scope['method'] in ('GET', 'HEAD') and headers.get('sec-fetch-mode') == 'navigate'
            if not navigation:
                await reject(403, 'Cross-origin requests are not allowed')
                return

        limit = 1024**2
        if path == '/api/transcribe':
            limit += MAX_MEDIA_BYTES
        elif path == '/api/dubbing/clone-sample':
            limit += MAX_CLONE_BYTES
        lengths = headers.getlist('content-length')
        if lengths:
            if len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdigit():
                await reject(400, 'Invalid request length')
                return
            if int(lengths[0]) > limit:
                await reject(413, 'Request exceeds the upload size limit')
                return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    raise HTTPException(413, 'Request exceeds the upload size limit')
            return message

        await self.app(scope, limited_receive, secure_send)
