"""Upload selected screenshots to TAPD; return one receipt per file, in input order."""

import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib import error, parse, request
from uuid import uuid4


class NoRedirect(request.HTTPRedirectHandler):
    """Never forward the user's bearer token or screenshot to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(base):
    u = parse.urlsplit(base)
    if (u.scheme != "https" or not u.hostname or u.username is not None or
            u.password is not None or u.query or u.fragment):
        raise ValueError("TAPD_API_BASE_URL must be the trusted HTTPS API base, without credentials")
    return base.rstrip("/") + "/files/upload_image"


def upload(files, workspace, url, token, open_request):
    receipts = []
    for index, file in enumerate(files, 1):
        path = Path(file)
        item = {"file": path.name, "image_src": None, "info": ""}
        receipts.append(item)
        mime = mimetypes.guess_type(path.name)[0]
        if not mime or not mime.startswith("image/"):
            item["info"] = "not an image file"
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            item["info"] = "cannot read file"
            continue
        ext = mimetypes.guess_extension(mime) or ".img"
        boundary = uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="workspace_id"\r\n\r\n{workspace}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="selftest-{index}{ext}"\r\n'
                f'Content-Type: {mime}\r\n\r\n').encode() + raw + f'\r\n--{boundary}--\r\n'.encode()
        req = request.Request(url, data=body, headers={
            "Authorization": "Bearer " + token, "Content-Type": "multipart/form-data; boundary=" + boundary
        }, method="POST")
        try:
            with open_request(req, timeout=60) as resp:
                payload = json.load(resp)
            data = payload.get("data") if isinstance(payload, dict) else None
            src = data.get("image_src") if isinstance(data, dict) else None
            if (isinstance(src, str) and src.startswith("/tfl/") and src.rstrip("/") != "/tfl"
                    and not any(c in src for c in '"<>\r\n')):
                item.update(image_src=src, info="ok")
            else:
                item["info"] = "no usable image reference; verify before retrying"
        except error.HTTPError as exc:
            item["info"] = f"HTTP {exc.code}; verify before retrying"
        except (OSError, ValueError) as exc:
            # Report type only: arbitrary server/connection error text can contain private data.
            item["info"] = f"{type(exc).__name__}; upload outcome unknown"
    return receipts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace_id")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()
    if not args.workspace_id.isascii() or not args.workspace_id.isdigit():
        ap.error("workspace_id must be the numeric TAPD workspace")
    token = os.environ.get("TAPD_ACCESS_TOKEN")
    if not token:
        ap.error("TAPD_ACCESS_TOKEN must be supplied via the authorized runtime environment")
    try:
        url = endpoint(os.environ.get("TAPD_API_BASE_URL") or "https://api.tapd.cn")
    except ValueError as exc:
        ap.error(str(exc))
    receipts = upload(args.files, args.workspace_id, url, token, request.build_opener(NoRedirect()).open)
    print(json.dumps(receipts, ensure_ascii=False, indent=2))
    return 0 if all(row["image_src"] for row in receipts) else 1


if __name__ == "__main__":
    sys.exit(main())
