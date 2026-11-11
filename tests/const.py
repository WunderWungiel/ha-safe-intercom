"""Shared test data: real responses captured from a SAFE S03M-class device."""

HOST = "192.0.2.10"
MAC = "00:5d:20:a0:35:12"

NETWORK_RESPONSE = (
    "BootProto=none\r\n"
    "IPAddress=192.0.2.10\r\n"
    "MACAddress=00-5d-20-a0-35-12\r\n"
    "HostName=Intercom\r\n"
    "Web Port=80\r\n"
)


def controller_response(
    status: tuple[int, int, int] = (0, 0, 0), controller_type: str = "3n"
) -> str:
    """Return a `controller_cgi?action=get` body as sent by the real device."""
    return (
        f"Type={controller_type}\r\n"
        "Output1=open\r\nDelay1=2\r\n"
        f"Status1={status[0]}\r\n"
        "CloseByCall1=close\r\nCloseByFace1=open\r\nCloseByNight1=close\r\n"
        "Output2=open\r\nDelay2=2\r\n"
        f"Status2={status[1]}\r\n"
        "CloseByCall2=close\r\nCloseByFace2=close\r\nCloseByNight2=close\r\n"
        "Output3=open\r\nDelay3=0\r\n"
        f"Status3={status[2]}\r\n"
        "CloseByCall3=close\r\nCloseByFace3=close\r\nCloseByNight3=close\r\n"
        "PlaySound=close\r\n"
    )
