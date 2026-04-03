from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser
import socket
import os

SERVICE_TYPE = "_nexsync._tcp.local."
SERVICE_NAME = socket.gethostname() + "._nexsync._tcp.local."

def register_service(port=47123):
    info = ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[socket.inet_aton(get_local_ip())],
        port=port,
        properties={"version": "2.0", "username": os.getlogin()}
    )
    zeroconf = Zeroconf()
    zeroconf.register_service(info)
    return zeroconf

def browse_services(callback):
    zeroconf = Zeroconf()
    browser = ServiceBrowser(zeroconf, SERVICE_TYPE, handler=callback)
    return zeroconf, browser