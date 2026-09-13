"""Minimal Windows minidump parser for crash triage.

No external deps. Extracts, for each .dmp:
  - exception code + (for access violations) faulting address
  - the faulting instruction address mapped to a loaded module
  - OS / CPU from MINIDUMP_SYSTEM_INFO
  - full module list (to see Qt/FFmpeg/Windows DLLs present)

Run: .venv/Scripts/python.exe scripts/analyze_minidump.py <dmp> [<dmp> ...]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

EXCEPTION_ACCESS_VIOLATION = 0xC0000005
EXCEPTION_STACK_OVERFLOW = 0xC00000FD
EXCEPTION_INT_DIVIDE_BY_ZERO = 0xC0000094
EXCEPTION_BREAKPOINT = 0x80000003
KNOWN_CODES = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000094: "INT_DIVIDE_BY_ZERO",
    0xC0000096: "PRIVILEGED_INSTRUCTION",
    0xC0000374: "HEAP_CORRUPTION",
    0xC0000409: "STACK_BUFFER_OVERRUN (/GS)",
    0xC0000602: "FAST_FAIL",
    0xC0000006: "IN_PAGE_ERROR",
    0x80000003: "BREAKPOINT",
    0xE06D7363: "C++ EXCEPTION (MSVC)",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC00000CC: "FLOAT_DIVIDE_BY_ZERO",
}

STREAM_THREAD_LIST = 0x0003
STREAM_MODULE_LIST = 0x0004
STREAM_EXCEPTION = 0x0006
STREAM_SYSTEM_INFO = 0x0007
STREAM_MISC = 0x000A
STREAM_MEMORY = 0x0009
STREAM_MEMORY64 = 0x000B


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def read_string(blob, stream_base, rva):
    """MINIDUMP_STRING at rva relative to the start of its stream:
    ULONG32 length (bytes) + UTF-16 buffer."""
    o = stream_base + rva
    length = u32(blob, o)
    if length <= 0 or length > 4096:
        return ""
    raw = blob[o + 4: o + 4 + length]
    return raw.decode("utf-16-le", errors="replace")


def parse_header(blob):
    if blob[:4] != b"MDMP":
        raise ValueError("not a minidump (signature=%r)" % blob[:4])
    version = u32(blob, 4)
    n_streams = u32(blob, 8)
    dir_rva = u32(blob, 12)
    return version, n_streams, dir_rva


def read_streams(blob):
    version, n_streams, dir_rva = parse_header(blob)
    streams = {}
    o = dir_rva
    for _ in range(n_streams):
        stype = u32(blob, o)
        size = u32(blob, o + 4)
        rva = u32(blob, o + 8)
        streams[stype] = (rva, size)
        o += 12
    return streams


def parse_exception(blob, rva, size):
    base = rva
    thread_id = u32(blob, base)
    # 4-byte alignment padding then MINIDUMP_EXCEPTION (152 bytes)
    ex = base + 8
    code = u32(blob, ex)
    flags = u32(blob, ex + 4)
    record = u64(blob, ex + 8)
    address = u64(blob, ex + 16)
    nparams = u32(blob, ex + 24)
    info = [u64(blob, ex + 32 + i * 8) for i in range(min(nparams, 15))]
    return {
        "thread_id": thread_id,
        "code": code,
        "flags": flags,
        "address": address,
        "params": info,
    }


def parse_modules(blob, rva, size):
    base = rva
    n = u32(blob, base)
    modules = []
    o = base + 4
    for _ in range(n):
        base_of_image = u64(blob, o)
        size_of_image = u32(blob, o + 8)
        name_rva = u32(blob, o + 20)
        name = read_string(blob, base, name_rva)
        modules.append((name, base_of_image, size_of_image))
        o += 108
    return modules


def parse_system_info(blob, rva, size):
    base = rva
    arch = struct.unpack_from("<H", blob, base)[0]
    # MINIDUMP_SYSTEM_INFO: ProcessorArchitecture(2) Level(2) Revision(2)
    # packed NumberOfProcessors/ProductType(2) pad(2) then Major(4) Minor(4)
    # Build(4) Platform(4) CSDVersionRva(4)
    major = u32(blob, base + 8)
    minor = u32(blob, base + 12)
    build = u32(blob, base + 16)
    platform = u32(blob, base + 20)
    csd_rva = u32(blob, base + 24)
    csd = read_string(blob, base, csd_rva) if csd_rva else ""
    arch_names = {0: "x86", 9: "amd64 (x64)", 5: "ARM", 12: "ARM64"}
    plat_names = {2: "Windows", 1: "Win9x"}
    return {
        "arch": arch_names.get(arch, str(arch)),
        "os": f"{plat_names.get(platform, str(platform))} {major}.{minor}.{build}",
        "csd": csd,
    }


def find_module(modules, address):
    for name, base, size in modules:
        if base <= address < base + size:
            return name, base, size
    return None, None, None


def read_memory_chunks(blob, streams):
    """Return dict: base_addr -> bytes, for MEMORY_LIST / MEMORY64_LIST."""
    chunks = {}
    if STREAM_MEMORY in streams:
        rva, size = streams[STREAM_MEMORY]
        base = rva
        n = u32(blob, base)
        o = base + 4
        for _ in range(n):
            start = u64(blob, o)
            rva_c = u32(blob, o + 8)
            csize = u32(blob, o + 12)
            chunks[start] = blob[rva_c: rva_c + csize]
            o += 16
    elif STREAM_MEMORY64 in streams:
        rva, size = streams[STREAM_MEMORY64]
        base = rva
        n = u64(blob, base)
        rva_data = u64(blob, base + 8)
        o = base + 16
        cursor = rva_data
        for _ in range(n):
            start = u64(blob, o)
            csize = u64(blob, o + 8)
            chunks[start] = blob[cursor: cursor + csize]
            cursor += csize
            o += 16
    return chunks


def read_ptr(chunks, addr):
    for base, data in chunks.items():
        if base <= addr < base + len(data):
            off = addr - base
            return struct.unpack_from("<Q", data, off)[0]
    return None


def walk_stack(blob, streams, thread_id, modules):
    """Resolve the crashing thread's stack frames to modules/functions."""
    # Locate the thread in THREAD_LIST matching thread_id, get its stack.
    if STREAM_THREAD_LIST not in streams:
        return []
    trva, tsize = streams[STREAM_THREAD_LIST]
    base = trva
    n = u32(blob, base)
    o = base + 4
    target = None
    for _ in range(n):
        tid = u32(blob, o)
        suspend = u32(blob, o + 4)
        teb = u64(blob, o + 8)
        stack_start = u64(blob, o + 16)
        stack_end = u64(blob, o + 24)
        if tid == thread_id:
            target = (stack_start, stack_end)
            break
        o += 48
    if not target:
        return []
    chunks = read_memory_chunks(blob, streams)
    frames = []
    # Walk RSP upward; on x64 the call stack is a linked list of return
    # addresses via the previous RSP stored at [RSP] (frame pointer omitted
    # builds). We scan 8-byte words and keep those that resolve to a module
    # and look like plausible return addresses (module + small offset).
    addr = target[0]
    seen = set()
    while addr < target[1] and len(frames) < 60:
        ptr = read_ptr(chunks, addr)
        if ptr is None:
            # jump to next memory chunk if available
            addr = (addr & ~0xFFF) + 0x1000
            continue
        if ptr not in seen and ptr != 0:
            mod, mbase, msize = find_module(modules, ptr)
            if mod:
                frames.append((ptr, mod, ptr - mbase))
                seen.add(ptr)
        addr += 8
    return frames


def module_for(modules, addr):
    return find_module(modules, addr)


def analyze(path: Path, verbose_modules: bool = False):
    blob = path.read_bytes()
    size_mb = len(blob) / 1_000_000
    print(f"\n{'='*70}")
    print(f"DUMP: {path.name}  ({size_mb:.1f} MB)")
    print(f"{'='*70}")
    streams = read_streams(blob)
    present = []
    for st in (STREAM_EXCEPTION, STREAM_MODULE_LIST, STREAM_SYSTEM_INFO, STREAM_MISC):
        if st in streams:
            present.append(st)
    print("streams present:", [hex(s) for s in streams])
    if STREAM_SYSTEM_INFO in streams:
        si = parse_system_info(blob, *streams[STREAM_SYSTEM_INFO])
        print(f"system: {si['arch']}  {si['os']}  {si['csd']}")
    if STREAM_MODULE_LIST in streams:
        modules = parse_modules(blob, *streams[STREAM_MODULE_LIST])
        print(f"loaded modules: {len(modules)}")
    else:
        modules = []
    if STREAM_EXCEPTION in streams:
        ex = parse_exception(blob, *streams[STREAM_EXCEPTION])
        code_name = KNOWN_CODES.get(ex["code"], hex(ex["code"]))
        print(f"EXCEPTION 0x{ex['code']:08X} ({code_name})  in thread {ex['thread_id']}")
        print(f"  faulting instruction address: 0x{ex['address']:016X}")
        if ex["code"] == EXCEPTION_ACCESS_VIOLATION and ex["params"]:
            op = "READ" if ex["params"][0] == 0 else ("WRITE" if ex["params"][0] == 1 else f"dep-{ex['params'][0]}")
            print(f"  access violation {op} at 0x{ex['params'][1]:016X}")
        mod, mbase, msize = find_module(modules, ex["address"])
        if mod:
            offset = ex["address"] - mbase
            print(f"  -> inside module: {mod}")
            print(f"     module base 0x{mbase:016X}  size 0x{msize:X}  offset 0x{offset:X}")
        else:
            print("  -> address not within any known module (likely unloaded/heap/JIT)")
    else:
        print("no exception stream (may be a kernel/other dump)")
    if STREAM_EXCEPTION in streams and modules:
        print("\nCRASHING THREAD CALL STACK (module + offset):")
        frames = walk_stack(blob, streams, ex["thread_id"], modules)
        if not frames:
            print("  (stack memory not captured in this dump)")
        for i, (ptr, mod, off) in enumerate(frames[:40]):
            print(f"  #{i:02d}  0x{ptr:016X}  {mod}  +0x{off:X}")
        # Highlight the deepest app/native frame that isn't ntdll/kernel32
        interesting = [
            (i, mod) for i, (_, mod, _) in enumerate(frames)
            if mod and not any(
                x in mod.lower() for x in
                ("ntdll", "kernel32", "kernelbase", "ucrtbase", "vcruntime",
                 "msvcrt", "user32", "win32u", "gdi32", "imm32", "combase",
                 "sechost", "rpcrt4", "advapi32", "ole32", "oleaut32"))
        ]
        if interesting:
            i, mod = interesting[0]
            print(f"  --> likely culprit near frame #{i}: {mod}")
    if verbose_modules and modules:
        print("\nALL MODULES:")
        for name in sorted(m for m, _, _ in modules):
            print(f"  {name}")
    return modules


def main(argv):
    dumps = [Path(p) for p in argv[1:]]
    if not dumps:
        print("usage: analyze_minidump.py <file.dmp> [...]")
        return 1
    for d in dumps:
        if not d.exists():
            print("NOT FOUND:", d)
            continue
        analyze(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
