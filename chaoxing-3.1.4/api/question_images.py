"""Bounded, authenticated question images; source URLs never enter the shared bank."""

import base64
import binascii
import copy
import hashlib
import io
import ipaddress
import logging
import re
import socket
import time
import warnings
from urllib.parse import urljoin, urlsplit

from PIL import Image, UnidentifiedImageError
import requests

from api.webai_errors import QuestionImageUnavailable, WebAIError

log = logging.getLogger(__name__)

MAX_IMAGES = 10
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_REQUEST_BYTES = 9 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
IMAGE_ID = re.compile(r"image_(?:[1-9]|10)")
IMAGE_MARKER = re.compile(r"\[(image_(?:[1-9]|10))\]")
SHA256 = re.compile(r"[a-f0-9]{64}")
MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
DEFAULT_IMAGE_HOSTS = ("chaoxing.com", "chaoxing.com.cn")


def image_manifest(question, *, require_hash=False):
    images = question.get("images", [])
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise WebAIError(f"A question may contain at most {MAX_IMAGES} images")
    content = question.get("title", "") + "\n" + str(question.get("options", ""))
    if "<img" in content.lower():
        raise WebAIError("Unprocessed image markup; the complete image manifest is required")
    references = set(IMAGE_MARKER.findall(content))
    identifiers = set()
    total = 0
    for index, image in enumerate(images, 1):
        if not isinstance(image, dict) or image.get("id") != f"image_{index}":
            raise WebAIError("Invalid or unordered question image identifiers")
        identifiers.add(image["id"])
        digest = image.get("sha256")
        if require_hash or digest is not None:
            if not isinstance(digest, str) or not SHA256.fullmatch(digest) \
                    or image.get("mime_type") not in MIMES:
                raise WebAIError("Question images need verified content fingerprints")
            size = image.get("byte_count")
            if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_IMAGE_BYTES:
                raise WebAIError("Invalid question image size")
            total += size
        elif not isinstance(image.get("source"), str):
            raise WebAIError("Question image source is missing")
    if references != identifiers or (question.get("has_images") and not images):
        raise WebAIError("Question images are missing or do not match their text positions")
    if total > MAX_TOTAL_IMAGE_BYTES:
        raise WebAIError("Question images exceed the combined size limit")
    return images


def inspect_image(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_IMAGE_BYTES:
        raise WebAIError("Question image is empty or exceeds the size limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise WebAIError("Question image dimensions exceed the pixel limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise WebAIError("Animated or multipage images require manual review")
                mime = Image.MIME.get(image.format)
                if mime not in MIMES and image.format not in {"BMP", "TIFF"}:
                    raise WebAIError("Unsupported question image format")
                image.load()
                if mime not in MIMES or image.format == "GIF" or "transparency" in image.info \
                        or "A" in image.getbands():
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    output = io.BytesIO()
                    background.save(output, format="PNG")
                    raw, mime = output.getvalue(), "image/png"
    except WebAIError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise WebAIError("Question image could not be decoded safely") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise WebAIError("Decoded question image exceeds the size limit")
    return raw, mime


def decode_image_data(image):
    value = image.get("data")
    if not isinstance(value, str) or not value or len(value) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise WebAIError("Question image data is missing or oversized")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WebAIError("Invalid question image encoding") from exc
    checked, mime = inspect_image(raw)
    # Prepared images are already normalized. Reject a changed representation.
    if checked != raw or mime != image.get("mime_type") \
            or len(raw) != image.get("byte_count") \
            or hashlib.sha256(raw).hexdigest() != image.get("sha256"):
        raise WebAIError("Question image does not match its verified fingerprint")
    return raw


def image_identity(question):
    return [
        [image["id"], image["sha256"], image["mime_type"], image["byte_count"]]
        for image in image_manifest(question, require_hash=True)
    ]


def split_image_data(question):
    """Validate once at the boundary, then persist small manifests and deduplicated BLOBs."""
    images = image_manifest(question, require_hash=True)
    clean = {key: question[key] for key in ("title", "type", "options", "course_id", "has_images")
             if key in question}
    blobs = []
    if images:
        clean["has_images"] = True
        clean["images"] = []
        for image in images:
            raw = decode_image_data(image)
            item = {key: image[key] for key in ("id", "sha256", "mime_type", "byte_count")}
            clean["images"].append(item)
            blobs.append((image["sha256"], image["mime_type"], raw, len(raw)))
    return clean, blobs


def _download_url(source, base_url, hosts):
    if not isinstance(source, str) or not source or len(source) > 8192 \
            or any(ord(char) < 32 for char in source):
        raise WebAIError("Question image URL is missing or invalid")
    url = urljoin(base_url, source)
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password \
                or port not in {80, 443} or not any(
                    host == allowed or host.endswith("." + allowed) for allowed in hosts
                ):
            raise WebAIError("Question image host is not allowed")
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise WebAIError("Private network addresses are not allowed for question images")
    except (ValueError, OSError) as exc:
        if isinstance(exc, WebAIError):
            raise
        raise WebAIError("Question image host could not be verified") from exc
    return url


class _Retryable(Exception):
    """Transient download problem: another attempt may well succeed."""


def _location(url):
    """scheme://host/path —— 能定位问题，又不泄露查询串里的签名。"""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:300]


def _download_once(session, url, hosts, deadline):
    for redirect in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebAIError("Question image download deadline exceeded")
        try:
            with session.get(
                url, stream=True, allow_redirects=False,
                timeout=(min(5, remaining), min(15, remaining)),
                headers={"Authorization": None},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = _download_url(response.headers.get("Location"), url, hosts)
                    if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                        raise WebAIError("Question image redirect would downgrade HTTPS")
                    url = target
                    continue
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    log.warning(
                        "Question image download refused: status=%s content_type=%r url=%s",
                        response.status_code, response.headers.get("Content-Type", ""), url[:300],
                    )
                    raise _Retryable(f"Question image download returned HTTP {response.status_code}")
                if response.status_code != 200:
                    log.warning(
                        "Question image download refused: status=%s content_type=%r url=%s",
                        response.status_code, response.headers.get("Content-Type", ""), url[:300],
                    )
                    raise QuestionImageUnavailable(
                        f"Question image download returned HTTP {response.status_code} "
                        f"({_location(url)})")
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if content_type and not content_type.startswith("image/") \
                        and content_type != "application/octet-stream":
                    log.warning(
                        "Question image is not an image: content_type=%r url=%s",
                        content_type, url[:300],
                    )
                    raise QuestionImageUnavailable(
                        f"Question image download did not return an image "
                        f"(content_type={content_type or 'none'}, {_location(url)})")
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isdigit() or int(declared) > MAX_IMAGE_BYTES):
                    raise WebAIError("Question image download exceeds the size limit")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    if time.monotonic() > deadline:
                        raise WebAIError("Question image download deadline exceeded")
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise WebAIError("Question image download exceeds the size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException as exc:
            raise _Retryable("Question image download failed; no image or answer was omitted") from exc
    raise WebAIError("Question image has too many redirects")


def _download_image(session, source, base_url, hosts, deadline, attempts=3):
    url = _download_url(source, base_url, hosts)
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(session, url, hosts, deadline)
        except _Retryable as exc:
            # 超星的图床偶发 502/超时：同一张图多试两次，别让整章测验白丢
            if attempt >= attempts or deadline - time.monotonic() <= 1.5:
                raise QuestionImageUnavailable(str(exc)) from None
            pause = min(1.5 * attempt, max(0.2, deadline - time.monotonic() - 0.5))
            log.info(
                "Question image download retries in %.1fs (%s/%s): %s",
                pause, attempt, attempts - 1, str(exc)[:120],
            )
            time.sleep(pause)


def prepare_question_images(question, session=None, *, base_url="https://mooc1.chaoxing.com/",
                            extra_hosts=(), on_progress=None):
    images = image_manifest(question)
    if not images:
        return question
    hosts = tuple(DEFAULT_IMAGE_HOSTS) + tuple(
        host.strip().lower() for host in extra_hosts if isinstance(host, str) and host.strip()
    )
    if any(not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host) for host in hosts):
        raise WebAIError("Invalid additional question image host")
    prepared = copy.deepcopy(question)
    downloaded = {}
    deadline, total = time.monotonic() + 60, 0
    for index, image in enumerate(prepared["images"], 1):
        if image.get("data"):
            raw = decode_image_data(image)
            mime = image["mime_type"]
        else:
            source = image.get("source", "")
            if source in downloaded:
                raw, mime = downloaded[source]
            else:
                if source.startswith("data:"):
                    match = re.fullmatch(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\r\n]+)", source)
                    if not match or len(match[1]) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                        raise WebAIError("Invalid or oversized inline question image")
                    try:
                        raw = base64.b64decode(re.sub(r"\s", "", match[1]), validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise WebAIError("Invalid inline question image encoding") from exc
                else:
                    if session is None:
                        raise WebAIError("An authenticated platform session is required to fetch images")
                    raw = _download_image(session, source, base_url, hosts, deadline)
                raw, mime = inspect_image(raw)
                downloaded[source] = raw, mime
        total += len(raw)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise WebAIError("Question images exceed the combined size limit")
        image.clear()
        image.update(
            id=f"image_{index}", sha256=hashlib.sha256(raw).hexdigest(), mime_type=mime,
            byte_count=len(raw), data=base64.b64encode(raw).decode("ascii"),
        )
        if on_progress is not None:
            on_progress(index, len(images))
    prepared["has_images"] = True
    image_manifest(prepared, require_hash=True)
    return prepared
