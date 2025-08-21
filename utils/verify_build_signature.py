import requests, hashlib, os, sys, json, datetime, platform
from build_info import VERSION
from pgpy import PGPKey, PGPSignature
import re
EMAIL_RE = re.compile(r"<([^>]+)>")

TRUSTED_FINGERPRINT = "19C7FE490539700D7A962F025B98078EFD704346"
PUBLIC_KEY_ARMORED = """
-----BEGIN PGP PUBLIC KEY BLOCK-----

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
=FJpr
-----END PGP PUBLIC KEY BLOCK-----

""".strip()

def detect_arch():
    m = platform.machine()
    if m in ("arm64","aarch64"): return "arm64"
    if m in ("x86_64","AMD64"):  return "x86_64"
    return "unknown"

def detect_platform():
    return "macos" if sys.platform=="darwin" else ("windows" if sys.platform=="win32" else "unknown")

def sha256_file(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

# Get executable path
def current_binary_path():
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0])

def _extract_uid_text(uid):
    name = getattr(uid, "name", None)
    email = getattr(uid, "email", None)
    if name or email:
        return name, email
    try:
        s = bytes(uid).decode("utf-8", "ignore")
    except Exception:
        s = str(uid)
    m = EMAIL_RE.search(s)
    email = m.group(1) if m else None
    name = s.split("<")[0].strip() if "<" in s else s
    return name or None, email or None

def verify_detached_with_pgpy(json_bytes: bytes, asc_text: str):
    key, _ = PGPKey.from_blob(PUBLIC_KEY_ARMORED)

    fp = key.fingerprint.replace(" ", "").upper()
    if fp != TRUSTED_FINGERPRINT.replace(" ", "").upper():
        return False, "fingerprint_mismatch", fp, None, None, None

    sig = PGPSignature.from_blob(asc_text)
    ok = key.verify(json_bytes, sig)
    if not ok:
        return False, "bad_signature", fp, None, None, None

    sig_time = getattr(sig, "created", None)
    if sig_time is None and hasattr(sig, "_signature"):
        sp = sig._signature.subpackets.hashed
        if "Signature Creation Time" in sp:
            sig_time = sp["Signature Creation Time"].created
    sig_iso = sig_time.astimezone(datetime.timezone.utc).isoformat() if sig_time else ""

    sig_user = None
    sig_email = None
    if key.userids:
        for uid in key.userids:
            name, email = _extract_uid_text(uid)
            if email:
                sig_user, sig_email = name or email, email
                break
        if sig_user is None:
            first_uid = next(iter(key.userids))
            name, email = _extract_uid_text(first_uid)
            sig_user, sig_email = (name or None), (email or None)

    if not sig_user:
        sig_user = fp

    return True, "ok", fp, sig_iso, sig_user, sig_email

def verify_build_signature(version, t=None):
    if t is None:
        t = lambda k, **kw: k.format(**kw)

    plat = detect_platform()
    arch = detect_arch()
    if plat == "unknown" or arch == "unknown":
        return {"status": 0, "message": t("verify.unsupported_platform_arch", plat=plat, arch=arch)}

    ver = version
    base = f"https://github.com/Nick-0506/match_image_finder/releases/download/v{ver}"
    binary_path = current_binary_path()
    if binary_path.endswith(".py") and not getattr(sys, "frozen", False):
        return {"status": 2, "message": t("app.verify_runpy")}

    json_name = f"builds_v{ver}_{plat}_{arch}.json"
    asc_name  = f"{json_name}.asc"
    json_url  = f"{base}/{json_name}"
    asc_url   = f"{base}/{asc_name}"

    try:
        headers = {"User-Agent": "MatchImageFinder/verify (PGPy)", "Cache-Control": "no-cache"}
        rj = requests.get(json_url, headers=headers, timeout=10)
        ra = requests.get(asc_url,  headers=headers, timeout=10)
        if rj.status_code != 200 or ra.status_code != 200:
            return {"status": 0, "message": t("verify.download_failed")}

        json_bytes = rj.content
        asc_text   = ra.text
    except Exception as e:
        return {"status": 0, "message": t("verify.network_error", err=str(e))}

    ok, why, fp, sig_iso, sig_user, sig_email = verify_detached_with_pgpy(json_bytes, asc_text)
    if not ok:
        if why == "fingerprint_mismatch":
            return {"status": 0, "message": t("verify.untrusted_key"), "fingerprint": fp}
        return {"status": 0, "message": t("verify.invalid_signature")}

    # Analysis JSON
    try:
        data = json.loads(json_bytes.decode("utf-8"))
        expected = (data.get("sha256") or "").lower()
        if not expected:
            return {"status": 0, "message": t("verify.bad_json", err="no sha256 field")}
    except Exception as e:
        return {"status": 0, "message": t("verify.bad_json", err=str(e))}

    actual = sha256_file(binary_path).lower()
    if actual != expected:
        return {"status": 0, "message": t("verify.hash_mismatch"),
                "actual_sha": actual, "expected_sha": expected}

    return {"status": 1, "message": t("verify.ok"),
            "signed_by": sig_user, "email": sig_email, "signature_date": sig_iso or "", "sha256": actual}