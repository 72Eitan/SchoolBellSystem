"""
ed25519_pure.py  -  חתימה דיגיטלית Ed25519 ב-Python רגיל (בלי ספריות חיצוניות)

מבוסס על המימוש לדוגמה של התקן RFC 8032 (סעיף 6). משמש לחתימה על חבילות עדכון (במחשב של היוצר)
ולאימות שלהן (במחשב הצלצולים, Python 3.8 / Windows 7). איטי יחסית (עשרות מילישניות), וזה לא משנה כאן.

  sign(secret32, message)            -> חתימה של 64 בתים
  verify(public32, message, sig64)   -> True / False
  secret_to_public(secret32)         -> מפתח ציבורי של 32 בתים
"""

import hashlib

p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p
_SQRT_M1 = pow(2, (p - 1) // 4, p)


def _sha512(s):
    return hashlib.sha512(s).digest()


def _sha512_modq(s):
    return int.from_bytes(_sha512(s), "little") % q


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p
    D = 2 * P[2] * Q[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    if (P[0] * Q[2] - Q[0] * P[2]) % p != 0:
        return False
    return (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _recover_x(y, sign):
    if y >= p:
        return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * _SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_GY = 4 * pow(5, p - 2, p) % p
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % p)


def _compress(P):
    zinv = pow(P[2], p - 2, p)
    x, y = P[0] * zinv % p, P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _expand(secret):
    if len(secret) != 32:
        raise ValueError("secret must be 32 bytes")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def secret_to_public(secret):
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret, msg):
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    Rs = _compress(_mul(r, _G))
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % q
    return Rs + int.to_bytes(s, 32, "little")


def verify(public, msg, signature):
    try:
        if len(public) != 32 or len(signature) != 64:
            return False
        A = _decompress(public)
        R = _decompress(signature[:32])
        if A is None or R is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= q:
            return False
        h = _sha512_modq(signature[:32] + public + msg)
        return _equal(_mul(s, _G), _add(R, _mul(h, A)))
    except Exception:
        return False


def update_message(version, sha256_hex):
    """מה בדיוק נחתם בחבילת עדכון: הגרסה + ה-hash של קובץ ה-zip. כך אי אפשר להחליף קובץ או לשנות גרסה."""
    return ("etratzon-update\n%s\n%s" % (version, sha256_hex.lower())).encode("utf-8")
