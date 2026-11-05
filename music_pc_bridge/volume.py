"""Read and change the Windows default multimedia output's master volume.

This uses the Core Audio endpoint API through ctypes so the background bridge
does not need a separate COM package.  Each call resolves the current default
render device, which also handles a user switching speakers while the bridge is
running.  The functions are synchronous and should be called from the bridge's
asyncio task, not from a BLE notification callback thread.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
from uuid import UUID


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )


def _guid(value: str) -> _GUID:
    # COM GUIDs use little-endian Data1, Data2, and Data3 fields.
    return _GUID.from_buffer_copy(UUID(value).bytes_le)


_CLSID_MM_DEVICE_ENUMERATOR = _guid("bcde0395-e52f-467c-8e3d-c4579291692e")
_IID_IMM_DEVICE_ENUMERATOR = _guid("a95664d2-9614-4f35-a746-de8db63617e6")
_IID_IAUDIO_ENDPOINT_VOLUME = _guid("5cdf2c82-841e-4546-9722-0cf74078229a")

_CLSCTX_ALL = 0x17
_COINIT_MULTITHREADED = 0
_RPC_E_CHANGED_MODE = 0x80010106
_ERENDER = 0
_EMULTIMEDIA = 1

# IAudioEndpointVolume vtable slots after IUnknown's three methods.
_SET_LEVEL_SCALAR = 7
_GET_LEVEL_SCALAR = 9
_SET_MUTE = 14
_GET_MUTE = 15


def _check_hresult(result: int, operation: str) -> None:
    if result & 0x80000000:
        raise OSError(f"{operation} failed: HRESULT 0x{result & 0xFFFFFFFF:08X}")


def _call(interface: ctypes.c_void_p, slot: int, *args: object,
          argtypes: tuple[type, ...] = ()) -> int:
    """Call one COM vtable method with its explicit Windows calling convention."""
    if not interface.value:
        raise OSError("Core Audio returned a null COM interface")
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[slot])
    result = method(interface, *args)
    _check_hresult(result, f"Core Audio method {slot}")
    return result


def _release(interface: ctypes.c_void_p) -> None:
    if interface.value:
        vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        release(interface)


@contextmanager
def _endpoint_volume():
    if os.name != "nt":
        raise OSError("Windows Core Audio is available only on Windows")

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = ()
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    ole32.CoCreateInstance.restype = ctypes.c_long

    init_result = ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    # Another library may already have initialized this thread as STA. In
    # that case COM is usable, but this call must not be balanced with an
    # extra CoUninitialize.
    if (init_result & 0xFFFFFFFF) != _RPC_E_CHANGED_MODE:
        _check_hresult(init_result, "CoInitializeEx")
    should_uninitialize = init_result in (0, 1)  # S_OK or S_FALSE.

    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    try:
        result = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_MM_DEVICE_ENUMERATOR), None, _CLSCTX_ALL,
            ctypes.byref(_IID_IMM_DEVICE_ENUMERATOR), ctypes.byref(enumerator),
        )
        _check_hresult(result, "CoCreateInstance(MMDeviceEnumerator)")
        if not enumerator.value:
            raise OSError("Core Audio device enumerator is unavailable")

        _call(
            enumerator, 4, _ERENDER, _EMULTIMEDIA, ctypes.byref(device),
            argtypes=(ctypes.c_int, ctypes.c_int,
                      ctypes.POINTER(ctypes.c_void_p)),
        )
        if not device.value:
            raise OSError("Windows has no default multimedia output device")
        _call(
            device, 3, ctypes.byref(_IID_IAUDIO_ENDPOINT_VOLUME), _CLSCTX_ALL,
            None, ctypes.byref(endpoint),
            argtypes=(ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
                      ctypes.POINTER(ctypes.c_void_p)),
        )
        if not endpoint.value:
            raise OSError("Default output has no Core Audio volume interface")
        yield endpoint
    finally:
        _release(endpoint)
        _release(device)
        _release(enumerator)
        if should_uninitialize:
            ole32.CoUninitialize()


def _read_percent(endpoint: ctypes.c_void_p) -> int:
    muted = ctypes.c_int()
    _call(endpoint, _GET_MUTE, ctypes.byref(muted),
          argtypes=(ctypes.POINTER(ctypes.c_int),))
    if muted.value:
        return 0
    level = ctypes.c_float()
    _call(endpoint, _GET_LEVEL_SCALAR, ctypes.byref(level),
          argtypes=(ctypes.POINTER(ctypes.c_float),))
    return max(0, min(100, round(level.value * 100)))


def get_volume_percent() -> int:
    """Return the current default multimedia output's effective 0–100 volume."""
    with _endpoint_volume() as endpoint:
        return _read_percent(endpoint)


def set_volume_percent(percent: int) -> int:
    """Set Windows master volume and return the effective 0–100 value.

    A positive slider value also unmutes the endpoint so the change is audible.
    Values outside 0–100 are clamped; Core Audio errors raise OSError.
    """
    requested = max(0, min(100, int(percent)))
    with _endpoint_volume() as endpoint:
        _call(endpoint, _SET_LEVEL_SCALAR, ctypes.c_float(requested / 100.0),
              None, argtypes=(ctypes.c_float, ctypes.c_void_p))
        if requested > 0:
            _call(endpoint, _SET_MUTE, 0, None,
                  argtypes=(ctypes.c_int, ctypes.c_void_p))
        return _read_percent(endpoint)
