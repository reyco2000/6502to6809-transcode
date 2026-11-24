"""decb.py - write Disk BASIC LOADM (DECB) binaries, including the paged
loading trick: a 1-byte segment that writes the MMU register $FFA2 maps a
physical 8K page at $4000-$5FFF, and the following segment fills it."""

MMU_SLOT2 = 0xFFA2
SLOT2_ADDR = 0x4000
BASIC_PAGE2 = 0x3A          # what Disk BASIC normally has at $4000


def segment(addr, data):
    data = bytes(data)
    assert 0 < len(data) <= 0xFFFF
    return bytes([0, len(data) >> 8, len(data) & 0xFF, addr >> 8, addr & 0xFF]) + data


def postamble(exec_addr):
    return bytes([0xFF, 0, 0, exec_addr >> 8, exec_addr & 0xFF])


def paged(pages):
    """pages: list of (physical_page, data<=8192).  Returns the segments,
    ending with slot 2 restored to BASIC's page."""
    out = bytearray()
    for page, data in pages:
        assert len(data) <= 0x2000
        out += segment(MMU_SLOT2, [page])
        out += segment(SLOT2_ADDR, data)
    out += segment(MMU_SLOT2, [BASIC_PAGE2])
    return bytes(out)


def split_pages(first_page, blob):
    return [(first_page + i, blob[i * 0x2000:(i + 1) * 0x2000])
            for i in range(-(-len(blob) // 0x2000))]
