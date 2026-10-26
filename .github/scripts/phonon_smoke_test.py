# Loads phonon.dll into the current Python process and creates a context and the default HRTF. Used to check that the
# Windows ARM64X DLL works in both ARM64 and x64 (emulated) processes.

import ctypes
import platform
import re
import struct
import sys
import sysconfig

dll_path, cmake_lists_path = sys.argv[1], sys.argv[2]

with open(cmake_lists_path) as f:
    major, minor, patch = (int(x) for x in re.search(r"project\(Phonon VERSION (\d+)\.(\d+)\.(\d+)\)", f.read()).groups())

class IPLContextSettings(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("logCallback", ctypes.c_void_p), ("allocateCallback", ctypes.c_void_p),
                ("freeCallback", ctypes.c_void_p), ("simdLevel", ctypes.c_int), ("flags", ctypes.c_int)]

class IPLAudioSettings(ctypes.Structure):
    _fields_ = [("samplingRate", ctypes.c_int32), ("frameSize", ctypes.c_int32)]

class IPLHRTFSettings(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("sofaFileName", ctypes.c_char_p), ("sofaData", ctypes.c_void_p),
                ("sofaDataSize", ctypes.c_int), ("volume", ctypes.c_float), ("normType", ctypes.c_int)]

# sysconfig.get_platform() reports the interpreter's own architecture (win-arm64 or win-amd64), whereas
# platform.machine() reports the machine's native architecture, even in an emulated x64 process.
print(f"Python {platform.python_version()}, {sysconfig.get_platform()} process on {platform.machine()}, {struct.calcsize('P') * 8}-bit")

phonon = ctypes.WinDLL(dll_path)

context = ctypes.c_void_p()
settings = IPLContextSettings(version=(major << 16) | (minor << 8) | patch)
assert phonon.iplContextCreate(ctypes.byref(settings), ctypes.byref(context)) == 0, "iplContextCreate failed"

hrtf = ctypes.c_void_p()
audio_settings = IPLAudioSettings(samplingRate=48000, frameSize=1024)
hrtf_settings = IPLHRTFSettings(type=0, volume=1.0)  # IPL_HRTFTYPE_DEFAULT, IPL_HRTFNORMTYPE_NONE
assert phonon.iplHRTFCreate(context, ctypes.byref(audio_settings), ctypes.byref(hrtf_settings), ctypes.byref(hrtf)) == 0, "iplHRTFCreate failed"

phonon.iplHRTFRelease(ctypes.byref(hrtf))
phonon.iplContextRelease(ctypes.byref(context))
print(f"Loaded {dll_path} and created a context and the default HRTF.")
