import ctypes
from ctypes import wintypes
import logging

logger = logging.getLogger(__name__)


def get_system_fonts():
    """Enumerate installed system fonts on Windows using GDI."""
    try:
        fonts = set()

        class LOGFONTW(ctypes.Structure):
            _fields_ = [
                ("lfHeight", wintypes.LONG),
                ("lfWidth", wintypes.LONG),
                ("lfEscapement", wintypes.LONG),
                ("lfOrientation", wintypes.LONG),
                ("lfWeight", wintypes.LONG),
                ("lfItalic", wintypes.BYTE),
                ("lfUnderline", wintypes.BYTE),
                ("lfStrikeOut", wintypes.BYTE),
                ("lfCharSet", wintypes.BYTE),
                ("lfOutPrecision", wintypes.BYTE),
                ("lfClipPrecision", wintypes.BYTE),
                ("lfQuality", wintypes.BYTE),
                ("lfPitchAndFamily", wintypes.BYTE),
                ("lfFaceName", wintypes.WCHAR * 32),
            ]

        EnumFontFamiliesExW = ctypes.windll.gdi32.EnumFontFamiliesExW
        EnumFontFamiliesExW.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(LOGFONTW),
            ctypes.WINFUNCTYPE(wintypes.INT, ctypes.POINTER(LOGFONTW), ctypes.c_void_p, wintypes.DWORD, wintypes.LPARAM),
            wintypes.LPARAM,
            wintypes.DWORD,
        ]
        EnumFontFamiliesExW.restype = wintypes.INT

        @ctypes.WINFUNCTYPE(wintypes.INT, ctypes.POINTER(LOGFONTW), ctypes.c_void_p, wintypes.DWORD, wintypes.LPARAM)
        def callback(lpelfe, lpntme, FontType, lParam):
            lf = lpelfe.contents
            name = lf.lfFaceName
            if name and not name.startswith("@"):
                fonts.add(name)
            return 1

        hdc = ctypes.windll.user32.GetDC(0)
        lf = LOGFONTW()
        lf.lfCharSet = 1  # DEFAULT_CHARSET to enumerate all fonts
        EnumFontFamiliesExW(hdc, ctypes.byref(lf), callback, 0, 0)
        ctypes.windll.user32.ReleaseDC(0, hdc)

        return sorted(fonts, key=lambda s: s.lower())
    except Exception as e:
        logger.error(f"Failed to enumerate system fonts: {e}")
        return []
