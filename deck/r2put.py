"""
r2put.py -- upload one file to Cloudflare R2, stdlib only.

Why this exists instead of rclone: the head unit's network is slow and
unreliable enough that `pkg install rclone` is a gamble, and rclone is a
~50 MB dependency to do one HTTP PUT. This is AWS SigV4 over urllib, which
Python already has, so the deck needs nothing beyond the interpreter.

Credentials come from an env-style file (secrets/r2-uploader.env in the
repo, ~/.scirocco-r2.env on the deck) holding a BUCKET-SCOPED R2 token --
Object Read+Write on scirocco-drives and nothing else. R2's S3 credentials
are derived from a Cloudflare API token: the Access Key ID is the token id,
and the Secret Access Key is the SHA-256 of the token value.

Region is always "auto" for R2, and the payload is hashed rather than sent
as UNSIGNED-PAYLOAD so a truncated upload fails the signature instead of
landing a corrupt object.
"""

import datetime
import hashlib
import hmac
import os
import urllib.error
import urllib.request

DEFAULT_ENV_PATHS = (
    os.path.expanduser("~/.scirocco-r2.env"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "secrets", "r2-uploader.env"),
)


class R2Error(Exception):
    pass


def load_env(path=None):
    """Read KEY=VALUE lines. Returns {} if no file is found, so callers can
    fall back to rclone rather than crashing."""
    paths = [path] if path else list(DEFAULT_ENV_PATHS)
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        out = {}
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        return out
    return {}


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret, datestamp, region, service):
    k = _sign(("AWS4" + secret).encode("utf-8"), datestamp)
    k = _sign(k, region)
    k = _sign(k, service)
    return _sign(k, "aws4_request")


def put(local_path, key, env=None, timeout=300, content_type=None):
    """PUT one local file at `key` within the bucket. True on success.

    `key` is bucket-relative, e.g. drives/2026/08/2026-08-23_1331/meta.json
    """
    env = env or load_env()
    for required in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                     "R2_ENDPOINT", "R2_BUCKET"):
        if not env.get(required):
            raise R2Error("missing %s in the R2 env file" % required)

    with open(local_path, "rb") as f:
        body = f.read()

    endpoint = env["R2_ENDPOINT"].rstrip("/")
    host = endpoint.split("://", 1)[-1]
    bucket = env["R2_BUCKET"]
    canonical_uri = "/%s/%s" % (bucket, key.lstrip("/"))

    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_headers = ("host:%s\nx-amz-content-sha256:%s\nx-amz-date:%s\n"
                         % (host, payload_hash, amzdate))
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers,
         payload_hash])

    region, service = "auto", "s3"
    scope = "%s/%s/%s/aws4_request" % (datestamp, region, service)
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amzdate, scope,
         hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(
        _signing_key(env["R2_SECRET_ACCESS_KEY"], datestamp, region, service),
        string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = ("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, "
            "Signature=%s" % (env["R2_ACCESS_KEY_ID"], scope,
                              signed_headers, signature))

    req = urllib.request.Request(endpoint + canonical_uri, data=body,
                                 method="PUT")
    req.add_header("Authorization", auth)
    req.add_header("x-amz-date", amzdate)
    req.add_header("x-amz-content-sha256", payload_hash)
    if content_type:
        req.add_header("Content-Type", content_type)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read()[:300].decode("utf-8", "replace")
        except Exception:
            pass
        raise R2Error("HTTP %s on %s: %s" % (e.code, key, detail))
    except Exception as e:
        raise R2Error("%s on %s" % (e, key))


def available(env=None):
    """True when credentials are present, so upload.py can choose a backend."""
    env = env or load_env()
    return all(env.get(k) for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                                    "R2_ENDPOINT", "R2_BUCKET"))


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: r2put.py <local-file> <bucket-relative-key>")
        raise SystemExit(2)
    print("uploaded" if put(sys.argv[1], sys.argv[2]) else "failed")
