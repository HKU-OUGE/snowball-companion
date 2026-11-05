"""HTTP services on numeric local/LAN addresses do not need reverse DNS."""
from http.server import ThreadingHTTPServer as Base
from socketserver import TCPServer


class ThreadingHTTPServer(Base):
    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]
