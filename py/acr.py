#!/usr/bin/env python3
import sys


def parse_hex(args):
    text = " ".join(args) if args else "FF 00 40 50 04 05 05 03 01"
    text = text.replace(",", " ").replace(":", " ")
    out = []
    for p in text.split():
        if p.lower().startswith("0x"):
            p = p[2:]
        out.append(int(p, 16))
    return out


def connect():
    from smartcard.CardConnection import CardConnection
    from smartcard.System import readers
    from smartcard.scard import SCARD_LEAVE_CARD, SCARD_SHARE_DIRECT

    rdr = None
    for r in readers():
        if "ACR122U" in str(r):
            rdr = r
            break
    if not rdr:
        raise RuntimeError("no ACR122U")
    conn = rdr.createConnection()
    try:
        conn.connect(mode=SCARD_SHARE_DIRECT, disposition=SCARD_LEAVE_CARD)
        return conn, CardConnection.RAW_protocol
    except Exception:
        conn.connect()
        return conn, None


def main():
    apdu = parse_hex(sys.argv[1:])
    conn, proto = connect()
    print(">>", " ".join(f"{b:02X}" for b in apdu))
    if proto is None:
        data, sw1, sw2 = conn.transmit(apdu)
    else:
        data, sw1, sw2 = conn.transmit(apdu, protocol=proto)
    body = " ".join(f"{b:02X}" for b in data)
    print("<<", (body + " " if body else "") + f"{sw1:02X} {sw2:02X}")
    conn.disconnect()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
