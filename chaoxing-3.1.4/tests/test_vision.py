import base64
import configparser
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from api.answer import Tiku
from api.decode import decode_questions_info
from api.question_images import (
    MAX_IMAGE_BYTES, MAX_IMAGE_REQUEST_BYTES, _download_url, decode_image_data,
    image_manifest, inspect_image, prepare_question_images, split_image_data,
)
from api.webai_client import (
    VisionUnavailableError, WebAIClient, WebAIError, parse_answers, question_bank_key,
)
from backend.service import Service, Settings, create_app
from webai_check import main as check_main


TOKEN = "offline-vision-test-token"
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
TEXT_QUESTION = {"title": "Practice", "type": "single", "options": "A. one\nB. two"}


def image_bytes(color="black", mode="RGB", format="PNG"):
    output = io.BytesIO()
    Image.new(mode, (16, 10), color).save(output, format=format)
    return output.getvalue()


def raw_question(*, count=1, color="black", course_id="101"):
    raw = image_bytes(color)
    return TEXT_QUESTION | {
        "course_id": course_id, "title": "Practice " + " ".join(
            f"[image_{index}]" for index in range(1, count + 1)
        ), "has_images": True, "images": [
            {"id": f"image_{index}", "source": "data:image/png;base64," + base64.b64encode(raw).decode()}
            for index in range(1, count + 1)
        ],
    }


def prepared_question(**kwargs):
    return prepare_question_images(raw_question(**kwargs))


class ImageExtractionTests(unittest.TestCase):
    def decode(self, title, options="", code="0", decoration=""):
        return decode_questions_info(
            '<form><div class="singleQuesId" data="1">'
            f'<div class="TiMu" data="{code}">{decoration}'
            f'<div class="Zy_TItle">{title}</div><ul>{options}</ul>'
            '</div></div></form>'
        )["questions"][0]

    def test_every_question_type_retains_stem_images_and_lazy_source(self):
        for code, kind in enumerate(("single", "multiple", "completion", "judgement", "shortanswer")):
            with self.subTest(kind=kind):
                question = self.decode('Formula <img src="loading.gif" data-src="/formula.png">', code=str(code))
                self.assertEqual(question["type"], kind)
                self.assertEqual(question["title"], "Formula [image_1]")
                self.assertEqual(question["images"], [{"id": "image_1", "source": "/formula.png"}])

    def test_option_letters_and_image_positions_survive_sorting(self):
        question = self.decode('<img src="/stem.png">', (
            '<li aria-label="B. second"><span>B</span><img data-original="/second.png"></li>'
            '<li aria-label="A. first"><img src="/first.png"></li>'
        ))
        self.assertEqual(question["options"], "A. first [image_3]\nB. [image_2]")
        self.assertEqual([image["source"] for image in question["images"]],
                         ["/stem.png", "/second.png", "/first.png"])
        self.assertEqual(len(image_manifest(question)), 3)

    def test_unlabelled_image_options_keep_dom_order_even_at_ten_images(self):
        question = self.decode("Choose", "".join(
            f'<li><img src="/{index}.png"></li>' for index in range(10)
        ))
        self.assertEqual(question["options"].splitlines()[9], "J. [image_10]")
        self.assertEqual(parse_answers('{"Answer":["J"]}', question), ["J. [image_10]"])

    def test_decorative_images_outside_stem_and_choices_do_not_change_text_questions(self):
        question = self.decode("Practice", '<li aria-label="A. one">one</li>',
                               decoration='<img src="/decorative.png">')
        self.assertFalse(question["has_images"])
        self.assertNotIn("images", question)


class ImageValidationTests(unittest.TestCase):
    def test_transparent_formulas_are_flattened_to_white_without_losing_pixels(self):
        raw, mime = inspect_image(image_bytes((0, 0, 0, 0), "RGBA"))
        self.assertEqual(mime, "image/png")
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))

    def test_static_supported_formats_decode_and_normalize(self):
        for format in ("PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"):
            with self.subTest(format=format):
                raw, mime = inspect_image(image_bytes(format=format))
                self.assertTrue(raw)
                self.assertIn(mime, {"image/png", "image/jpeg", "image/webp"})

    def test_invalid_animation_svg_and_size_limits_fail_closed(self):
        animation = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(
            animation, format="GIF", save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")],
        )
        for raw in (b"", b"<svg></svg>", b"broken-png", b"x" * (MAX_IMAGE_BYTES + 1), animation.getvalue()):
            with self.subTest(size=len(raw)), self.assertRaises(WebAIError):
                inspect_image(raw)
        with patch("api.question_images.MAX_IMAGE_PIXELS", 10), self.assertRaises(WebAIError):
            inspect_image(image_bytes())

    def test_preparation_strips_source_and_checks_hash_mime_and_encoding(self):
        question = prepared_question(count=2)
        for image in question["images"]:
            self.assertNotIn("source", image)
            self.assertEqual(decode_image_data(image), image_bytes())
        for change in ({"data": "not-base64"}, {"sha256": "a" * 64},
                       {"mime_type": "image/jpeg"}, {"byte_count": 1}):
            with self.subTest(change=change), self.assertRaises(WebAIError):
                decode_image_data(question["images"][0] | change)
        clean, blobs = split_image_data(question | {"private_cookie": "never-store"})
        self.assertNotIn("data", clean["images"][0])
        self.assertNotIn("private_cookie", clean)
        self.assertEqual(blobs[0][2], image_bytes())

    def test_incomplete_unordered_and_excessive_manifests_are_rejected(self):
        question = raw_question()
        for changed in (
            question | {"images": []}, question | {"images": None},
            question | {"title": "Missing position"},
            question | {"images": [{"id": "image_2", "source": "/a.png"}]},
            question | {"title": '<img src="/a.png">'},
            raw_question(count=11),
        ):
            with self.subTest(title=changed["title"]), self.assertRaises(WebAIError):
                image_manifest(changed)

    def test_combined_limit_counts_all_attachments_not_only_distinct_blobs(self):
        with patch("api.question_images.MAX_TOTAL_IMAGE_BYTES", len(image_bytes()) + 1):
            with self.assertRaisesRegex(WebAIError, "combined"):
                prepare_question_images(raw_question(count=2))

    def test_identity_uses_actual_pixels_not_temporary_source_urls(self):
        question = prepared_question()
        same = copy.deepcopy(question)
        same["images"][0]["source"] = "https://p.chaoxing.com/private?token=other"
        changed = prepared_question(color="red")
        self.assertEqual(question_bank_key(question), question_bank_key(same))
        self.assertNotEqual(question_bank_key(question), question_bank_key(changed))
        self.assertTrue(question_bank_key(question).startswith("course-bank:v2:"))
        text = TEXT_QUESTION | {"course_id": "101"}
        material = [1, "101", "single", "Practice", [("A", "one"), ("B", "two")]]
        digest = hashlib.sha256(json.dumps(material, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(question_bank_key(text), "course-bank:v1:" + digest)


class ImageDownloadTests(unittest.TestCase):
    def response(self, *, status=200, headers=None, chunks=None):
        response = Mock(status_code=status, headers=headers or {"Content-Type": "image/png"})
        response.iter_content.return_value = chunks or [image_bytes()]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    @patch("api.question_images.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_authenticated_downloads_resolve_relative_urls_and_deduplicate(self, dns):
        session = Mock()
        session.get.return_value = self.response()
        question = raw_question(count=2)
        for image in question["images"]:
            image["source"] = "../images/formula.png"
        result = prepare_question_images(question, session, base_url="https://mooc1.chaoxing.com/work/page")
        session.get.assert_called_once()
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://mooc1.chaoxing.com/images/formula.png")
        self.assertEqual(kwargs["headers"], {"Authorization": None})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(result["images"][0]["sha256"], result["images"][1]["sha256"])

    def test_private_untrusted_and_credential_urls_are_blocked_before_network(self):
        for source in ("http://127.0.0.1/a", "https://evil.invalid/a",
                       "https://chaoxing.com.evil.invalid/a", "file:///tmp/a",
                       "https://user:password@p.chaoxing.com/a", "https://p.chaoxing.com:22/a"):
            with self.subTest(source=source), self.assertRaises(WebAIError):
                _download_url(source, "", ("chaoxing.com",))
        for address in ("127.0.0.1", "10.0.0.1", "::1", "169.254.169.254"):
            with self.subTest(address=address), patch(
                "api.question_images.socket.getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
            ), self.assertRaises(WebAIError):
                _download_url("https://p.chaoxing.com/a", "", ("chaoxing.com",))

    @patch("api.question_images.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_a_flaky_image_host_is_retried_before_giving_up(self, dns):
        """超星图床偶发 502/超时：同一张图再试两次，别让整章测验白丢。"""
        session = Mock()
        session.get.side_effect = [
            self.response(status=502, headers={}),
            self.response(status=503, headers={}),
            self.response(),
        ]
        question = raw_question()
        question["images"][0]["source"] = "https://p.chaoxing.com/a"
        with patch("api.question_images.time.sleep"):
            result = prepare_question_images(question, session)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(result["images"][0]["byte_count"], len(image_bytes()))

    @patch("api.question_images.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_a_missing_image_is_not_retried(self, dns):
        session = Mock()
        session.get.return_value = self.response(status=404, headers={})
        question = raw_question()
        question["images"][0]["source"] = "https://p.chaoxing.com/a"
        with patch("api.question_images.time.sleep"), self.assertRaises(WebAIError):
            prepare_question_images(question, session)
        self.assertEqual(session.get.call_count, 1)

    @patch("api.question_images.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_redirects_content_types_and_stream_limits_never_drop_bad_images(self, dns):
        for first in (
            self.response(status=302, headers={"Location": "http://p.chaoxing.com/a"}),
            self.response(status=302, headers={"Location": "https://evil.invalid/a"}),
            self.response(headers={"Content-Type": "text/html"}),
            self.response(headers={"Content-Length": str(MAX_IMAGE_BYTES + 1)}),
            self.response(chunks=[b"x" * MAX_IMAGE_BYTES, b"x"]),
        ):
            session = Mock()
            session.get.return_value = first
            question = raw_question()
            question["images"][0]["source"] = "https://p.chaoxing.com/a"
            with self.subTest(headers=first.headers), self.assertRaises(WebAIError):
                prepare_question_images(question, session)


class VisionHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        encoded = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self.server.gets.append(self.path)
        self.send_json({"data": self.server.models})

    def do_POST(self):
        self.server.received.append((
            self.path, self.headers.get("Authorization"),
            json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
        ))
        status = self.server.model_failures.get(self.server.received[-1][2]["model"], 200)
        if status != 200:
            self.send_json({"error": {"message": "private-upstream-secret"}}, status)
            return
        content = self.server.received[-1][2]["messages"][0]["content"]
        prompt = content[0]["text"] if isinstance(content, list) else content
        if prompt.startswith("STAGE: IMAGE_READING."):
            identifiers = list(dict.fromkeys(re.findall(r'"id": "(image_[0-9]+)"', prompt)))
            answer = json.dumps({"images": [
                {"id": identifier, "readable": True, "text": "practice content"}
                for identifier in identifiers
            ]})
        elif prompt.startswith("STAGE: IMAGE_VERIFICATION."):
            answer = '{"readable":true,"reading_matches":true,"Answer":["B"]}'
        else:
            answer = '{"Answer":["B"]}'
        self.send_json({"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]})


class VisionClientTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), VisionHandler)
        self.server.models = [
            {"id": "text", "image_policy": "forbidden", "type": "text"},
            {"id": "draw", "image_policy": "optional", "type": "image"},
            {"id": "vision", "image_policy": "optional", "type": "text"},
        ]
        self.server.received, self.server.gets = [], []
        self.server.model_failures = {}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.config = {
            "webai_endpoint": f"http://127.0.0.1:{self.server.server_port}/v1",
            "webai_model": "text", "webai_key": TOKEN, "webai_key_env": "OFFLINE_VISION_TEXT_KEY",
            "webai_stream": "false", "min_interval_seconds": "0",
        }
        environment = patch.dict(os.environ, {"OFFLINE_VISION_TEXT_KEY": "", "WEBAI_VISION_API_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def client(self, **updates):
        client = WebAIClient(self.config | updates)
        self.addCleanup(client.close)
        return client

    def check_cli(self, arguments, files=(), **updates):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            config = configparser.ConfigParser(interpolation=None)
            config["tiku"] = self.config | updates
            with config_path.open("w", encoding="utf-8") as stream:
                config.write(stream)
            args = ["--config", str(config_path), *arguments]
            for index, content in enumerate(files):
                filename = Path(directory) / f"practice-{index}.png"
                if content is not None:
                    filename.write_bytes(content)
                args.extend(["--image", str(filename)])
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = check_main(args)
            return status, output.getvalue(), error.getvalue()

    def test_auto_routes_multiple_images_and_text_to_different_models(self):
        client = self.client()
        question = prepared_question(count=2)
        self.assertEqual(parse_answers(client.complete(question), question), ["B. two"])
        self.assertEqual(client.selected_vision_model, "vision")
        path, token, request = self.server.received[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(token, "Bearer " + TOKEN)
        content = request["messages"][0]["content"]
        attachments = [part for part in content if part["type"] == "image_url"]
        self.assertEqual(len(attachments), 2)
        self.assertTrue(all(part["image_url"]["detail"] == "high" for part in attachments))
        self.assertIn("[image_2]", content[-2]["text"])
        self.assertNotIn("source", json.dumps(request))
        client.complete(TEXT_QUESTION)
        self.assertEqual(self.server.received[-1][2]["model"], "text")
        self.assertEqual(self.server.gets, ["/v1/models"])

    def test_missing_or_text_only_vision_worker_does_not_make_a_text_request(self):
        for models in ([], [{"id": "text", "image_policy": "forbidden", "type": "text"}],
                       [{"id": "unknown-capability"}]):
            self.server.models = models
            with self.subTest(models=models), self.assertRaises(VisionUnavailableError):
                self.client().complete(prepared_question())
        self.assertEqual(self.server.received, [])

    def test_option_images_are_attached_in_question_order_with_explicit_positions(self):
        question = prepared_question(count=3) | {
            "title": "Stem [image_1]", "options": "A. [image_3]\nB. [image_2]",
        }
        for index, color in ((1, "red"), (2, "blue")):
            image = prepared_question(color=color)["images"][0]
            question["images"][index] = image | {"id": f"image_{index + 1}"}
        content = self.client().request_content(question)
        labels = [part["text"] for part in content[1:] if part["type"] == "text"]
        self.assertEqual(labels, [
            "\nAttached image [image_1] for stem:\n",
            "\nAttached image [image_3] for option A:\n",
            "\nAttached image [image_2] for option B:\n",
        ])
        expected = [question["images"][index]["data"] for index in (0, 2, 1)]
        actual = [part["image_url"]["url"].split(",")[1] for part in content if part["type"] == "image_url"]
        self.assertEqual(actual, expected)

    def test_reused_image_marker_keeps_all_positions_without_duplicate_attachments(self):
        question = prepared_question() | {"options": "A. [image_1]\nB. text"}
        content = self.client().request_content(question)
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 1)
        self.assertIn("for stem, option A", content[1]["text"])
        self.assertIn('"positions": ["stem", "option A"]', content[0]["text"])

    def test_image_fallback_never_uses_text_or_image_generation_models(self):
        self.server.models.append({"id": "vision-backup", "image_policy": "optional", "type": "text"})
        self.server.model_failures["vision"] = 503
        client = self.client(webai_vision_model="vision", webai_vision_model_fallbacks=[
            "text", "draw", "vision-backup",
        ])
        question = prepared_question(count=2)
        self.assertEqual(parse_answers(client.complete(question), question), ["B. two"])
        requests = [request for _, _, request in self.server.received]
        self.assertEqual([request["model"] for request in requests], ["vision", "vision-backup"])
        self.assertEqual(requests[0]["messages"], requests[1]["messages"])
        self.assertEqual(client.selected_vision_model, "vision-backup")
        self.assertEqual(client.selected_model, "vision-backup")

    def test_offline_primary_can_use_an_explicitly_configured_capable_fallback(self):
        client = self.client(webai_vision_model="offline", webai_vision_model_fallbacks=["text", "vision"])
        client.complete(prepared_question())
        self.assertEqual([request["model"] for _, _, request in self.server.received], ["vision"])

    def test_corrupt_images_do_not_trigger_any_fallback_or_send_a_partial_question(self):
        question = prepared_question(count=2)
        question["images"][1]["data"] = "not-base64"
        with self.assertRaises(WebAIError):
            self.client(webai_vision_model_fallbacks=["vision"]).complete(question)
        self.assertEqual(self.server.received, [])
        self.assertEqual(self.server.gets, [])

    def test_vision_cache_includes_both_routes_but_does_not_change_shared_identity(self):
        question = prepared_question()
        baseline = self.client()
        vision = self.client(webai_vision_model_fallbacks=["vision-backup"])
        text = self.client(webai_model_fallbacks=["text-backup"])
        self.assertTrue(vision.cache_key(question).startswith("webai:vision:v3:"))
        self.assertNotEqual(vision.cache_key(question), baseline.cache_key(question))
        self.assertNotEqual(text.cache_key(question), baseline.cache_key(question))
        self.assertTrue(question_bank_key(question).startswith("course-bank:v2:"))

    def test_separate_endpoint_never_reuses_text_credential_and_explicit_model_needs_no_models(self):
        client = self.client(webai_vision_endpoint=self.config["webai_endpoint"] + "/vision",
                             webai_vision_model="specific-vision")
        with self.assertRaises(VisionUnavailableError):
            client.complete(prepared_question())
        self.assertEqual(self.server.received, [])
        client = self.client(webai_vision_endpoint=self.config["webai_endpoint"] + "/vision",
                             webai_vision_model="specific-vision", webai_vision_key="independent-key")
        client.complete(prepared_question())
        path, token, request = self.server.received[0]
        self.assertEqual(path, "/v1/vision/chat/completions")
        self.assertEqual(token, "Bearer independent-key")
        self.assertEqual(request["model"], "specific-vision")
        self.assertEqual(self.server.gets, [])

    def test_explicit_text_bridge_model_cannot_be_used_for_vision(self):
        with self.assertRaises(VisionUnavailableError):
            self.client(webai_vision_model="text").complete(prepared_question())
        self.assertEqual(self.server.received, [])

    def test_all_answer_types_accept_complete_image_questions(self):
        client = self.client()
        for kind, payload, expected in (
            ("single", ["B"], ["B. two"]), ("multiple", ["A", "B"], ["A. one", "B. two"]),
            ("judgement", [True], ["true"]), ("completion", ["one", "two"], ["one", "two"]),
            ("shortanswer", ["Explanation"], ["Explanation"]),
        ):
            question = prepared_question() | {"type": kind}
            with self.subTest(kind=kind):
                self.assertIsInstance(client.request_content(question), list)
                self.assertEqual(parse_answers(json.dumps({"Answer": payload}), question), expected)

    def test_provider_cache_and_events_do_not_expose_image_data(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config | {
                "provider": "WebAI", "submit": "false", "webai_preview": "true",
                "experimental_vision": "true",
                "webai_cache": str(Path(directory) / "cache.json"),
                "true_list": "true", "false_list": "false", "cover_rate": "1",
            }
            tiku = Tiku()
            tiku.config_set(config)
            provider = tiku.get_tiku_from_config()
            provider.init_tiku()
            self.addCleanup(provider.client.close)
            with patch("api.answer.report") as report:
                self.assertIsNotNone(provider.query(raw_question()))
                self.assertIsNotNone(provider.query(raw_question()))
            self.assertEqual(len(self.server.received), 3)
            self.assertEqual(
                [request["model"] for _, _, request in self.server.received],
                ["vision", "text", "vision"],
            )
            self.assertIn("downloading_images", [call.kwargs["ai_state"] for call in report.call_args_list])
            self.assertNotIn("base64", str(report.call_args_list))
            self.assertNotIn("data:image", Path(config["webai_cache"]).read_text())
            self.assertIsNone(provider.query(TEXT_QUESTION | {"images": None}))

    def test_experimental_vision_disabled_fails_closed_before_image_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.config | {
                "provider": "WebAI", "submit": "false", "webai_preview": "true",
                "webai_cache": str(Path(directory) / "cache.json"),
                "true_list": "true", "false_list": "false", "cover_rate": "1",
            }
            tiku = Tiku()
            tiku.config_set(config)
            provider = tiku.get_tiku_from_config()
            provider.init_tiku()
            self.addCleanup(provider.client.close)
            with patch("api.answer.report") as report:
                self.assertIsNone(provider.query(raw_question()))
            self.assertEqual(self.server.received, [])
            self.assertEqual(self.server.gets, [])
            self.assertFalse(Path(config["webai_cache"]).exists())
            self.assertEqual(
                [call.kwargs["ai_state"] for call in report.call_args_list],
                ["vision_disabled"],
            )

    def test_image_cli_preserves_option_positions_without_contacting_a_course(self):
        status, output, error = self.check_cli(
            ["--question", "Choose the correct diagram", "--option", "A. [image_1]",
             "--option", "B. [image_2]"], [image_bytes(), image_bytes("red")],
        )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(json.loads(output), {
            "model": "vision", "image_count": 2, "Answer": ["B. [image_2]"], "course_changed": False,
        })
        self.assertEqual(len(self.server.received), 1)
        path, token, request = self.server.received[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(request["model"], "vision")
        content = request["messages"][0]["content"]
        self.assertIn('"options": ["A. [image_1]", "B. [image_2]"]', content[0]["text"])
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 2)
        self.assertNotIn(token, output)
        self.assertNotIn("base64", output)
        self.assertEqual(self.server.gets, ["/v1/models"])

    def test_image_cli_adds_missing_markers_and_accepts_an_independent_explicit_model(self):
        status, output, error = self.check_cli(
            ["--question", "Read the diagram", "--type", "shortanswer"], [image_bytes()],
            webai_vision_endpoint=self.config["webai_endpoint"] + "/vision",
            webai_vision_model="specific-vision", webai_vision_key="independent-key",
        )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(json.loads(output)["model"], "specific-vision")
        self.assertFalse(json.loads(output)["course_changed"])
        self.assertEqual(self.server.gets, [])
        path, token, request = self.server.received[0]
        self.assertEqual(path, "/v1/vision/chat/completions")
        self.assertEqual(token, "Bearer independent-key")
        self.assertIn(r"Read the diagram\n[image_1]", request["messages"][0]["content"][0]["text"])
        self.assertNotIn("independent-key", output)

    def test_image_cli_rejects_unreadable_corrupt_and_oversized_files_before_generation(self):
        for raw in (None, b"not-an-image", b"x" * (MAX_IMAGE_BYTES + 1)):
            with self.subTest(size=None if raw is None else len(raw)):
                status, output, error = self.check_cli(
                    ["--question", "Read the diagram", "--type", "shortanswer"], [raw],
                )
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                self.assertIn("WebAI check failed:", error)
                self.assertNotIn(TOKEN, error)
        self.assertEqual(self.server.received, [])
        self.assertEqual(self.server.gets, [])

    def test_vision_cli_inspects_capabilities_without_generating_or_reading_accounts(self):
        status, output, error = self.check_cli(["--vision-models"])
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(json.loads(output)["vision_models"], self.server.models)
        self.assertEqual(self.server.received, [])
        self.assertTrue(all(path == "/v1/models" for path in self.server.gets))
        self.server.models = [{"id": "text", "image_policy": "forbidden", "type": "text"}]
        status, output, error = self.check_cli(["--vision-models"])
        self.assertEqual(status, 1)
        self.assertIn("No image-capable AI worker", error)
        self.assertEqual(self.server.received, [])


class ImageBankFixtures:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        calls = self.calls
        class FakeAI:
            def complete(self, question):
                calls.append(question)
                return '{"Answer":["B"]}'
            def close(self):
                pass
        self.service = Service(Settings(state_dir=Path(self.directory.name)), client_factory=FakeAI)
        self.addCleanup(self.service.close)
        self.store = self.service.store
        self.client = create_app(self.service).test_client()
        self.headers = {"Authorization": "Bearer " + self.service.secrets.token}

    def job(self, course_ids=None):
        account = self.store.save_account(f"Offline {len(self.store.accounts())}", os.urandom(8).hex(), "offline-only")
        job, _ = self.store.create_job(account["id"], {
            "course_ids": course_ids or ["101"], "videos_only": False, "submit": False, "preview": True,
        })
        self.store.set_state(job["id"], "running")
        return job

    def wait_answer(self, job_id, answer_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            value = self.store.answer(job_id, answer_id)
            if value["state"] in {"done", "error"}:
                return value
            time.sleep(0.01)
        self.fail("Offline AI broker did not finish")


class ImageBankTests(ImageBankFixtures, unittest.TestCase):
    def test_thirty_accounts_share_one_generation_and_deduplicated_blob(self):
        jobs = [self.job() for _ in range(30)]
        question = prepared_question(count=2)
        with ThreadPoolExecutor(max_workers=30) as pool:
            results = list(pool.map(lambda job: self.service.broker.request(job["id"], question), jobs))
        answer_id = results[0]["id"]
        self.assertEqual({result["id"] for result in results}, {answer_id})
        self.assertEqual(sum(result["source"] == "shared" for result in results), 29)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM answer_images").fetchone()[0], 1)
            stored = db.execute("SELECT question FROM answers WHERE id=?", (answer_id,)).fetchone()[0]
        self.assertNotIn("base64", stored)
        self.assertNotIn('"data"', stored)
        self.assertNotIn('"source"', stored)
        self.service.broker.start()
        self.assertEqual(self.wait_answer(jobs[0]["id"], answer_id)["state"], "done")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(decode_image_data(self.calls[0]["images"][0]), image_bytes())
        candidate = self.store.answer(jobs[0]["id"], answer_id)
        self.assertEqual(candidate["validation"], "format_checked")
        replacement = self.service.broker.request(jobs[-1]["id"], question)
        self.assertEqual((replacement["state"], replacement["source"]), ("queued", "ai"))
        self.assertNotEqual(replacement["id"], answer_id)
        self.assertEqual(self.store.storage_summary()["records"]["answer_images"], 1)

    def test_image_inspection_is_authenticated_scoped_and_does_not_start_ai(self):
        job = self.job()
        question = prepared_question(count=2)
        answer = self.service.broker.request(job["id"], question)
        path = f"/v1/question-bank/{answer['id']}/images/image_2"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)
        self.store.finish_answer(answer["id"], '{"Answer":["B"]}')
        response = self.client.get(path, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data_url"].split(",")[1], question["images"][1]["data"])
        self.assertEqual(self.client.get(path.replace("image_2", "image_3"), headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(path.replace("image_2", "image_11"), headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(path, headers=self.headers, json={}).status_code, 405)
        detail = self.service.question_bank.detail(answer["id"])
        self.assertEqual(detail["image_count"], 2)
        self.assertNotIn('"data"', json.dumps(detail))
        storage = self.service.question_bank.stats()["storage"]
        self.assertEqual(storage["image_count"], 1)
        self.assertEqual(storage["image_bytes"], len(image_bytes()))
        self.assertEqual(self.calls, [])

    def test_blob_integrity_and_request_identity_are_verified(self):
        job = self.job()
        question = prepared_question()
        with self.assertRaises(WebAIError):
            self.store.request_answer(job["id"], "wrong-image-key", question)
        result = self.service.broker.request(job["id"], question)
        with self.store.transaction() as db:
            db.execute("UPDATE answer_images SET content=?", (b"corrupt",))
        with self.assertRaisesRegex(WebAIError, "integrity"):
            self.store.hydrate_answer_images(question)
        self.store.finish_answer(result["id"], '{"Answer":["B"]}')
        response = self.client.get(
            f"/v1/question-bank/{result['id']}/images/image_1", headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("corrupt", response.text)

    def test_image_limits_are_only_relaxed_for_authenticated_loopback_workers(self):
        job = self.job()
        payload = prepared_question() | {"ignored": "x" * 70000}
        with patch.object(self.service.scheduler, "authenticate_worker", return_value=job["id"]):
            response = self.client.post("/internal/answers", headers={"Authorization": "Bearer offline"}, json=payload)
            self.assertEqual(response.status_code, 202, response.json)
            remote = self.client.post("/internal/answers", headers=self.headers, json=payload,
                                      environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
            self.assertEqual(remote.status_code, 401)
            huge = self.client.post("/internal/answers", headers=self.headers,
                                    data=b" " * (MAX_IMAGE_REQUEST_BYTES + 1), content_type="application/json")
            self.assertEqual(huge.status_code, 413)
        normal = self.client.post("/v1/accounts", headers=self.headers, json=payload)
        self.assertEqual(normal.status_code, 413)

    def test_missing_vision_does_not_cool_down_text_answers(self):
        job = self.job()
        def complete(question):
            if question.get("images"):
                raise VisionUnavailableError("Offline test: no visual worker")
            return '{"Answer":["B"]}'
        fake = Mock(complete=Mock(side_effect=complete))
        self.service.broker.client_factory = lambda: fake
        image = self.service.broker.request(job["id"], prepared_question())
        text = self.service.broker.request(job["id"], TEXT_QUESTION)
        self.service.broker.start()
        self.assertEqual(self.wait_answer(job["id"], image["id"])["error"], "AI_VISION_UNAVAILABLE")
        self.assertEqual(self.wait_answer(job["id"], text["id"])["state"], "done")
        self.assertEqual(self.service.broker.cooldown_until, 0)

    def test_image_api_answers_in_one_direct_request_by_default(self):
        job = self.job()
        self.client.put("/v1/ai/provider", headers=self.headers, json={
            "endpoint": "https://api.example.com/v1", "model": "api-text", "api_key": "offline-private-key",
        })
        self.client.put("/v1/ai/channel", headers=self.headers, json={"channel": "api", "kind": "vision"})
        self.assertTrue(self.service.broker.direct_vision())

        calls = []

        class DirectClient:
            last_diagnostics = None
            selected_model = "api-vision"
            context = {}

            def complete_direct(self, question):
                calls.append(len(question.get("images") or []))
                return ["B"]

            def complete_vision(self, *args, **kwargs):
                raise AssertionError("direct mode must not run the three-stage pipeline")

            def complete(self, question):
                raise AssertionError("direct mode must send the images, not a text prompt")

            def close(self):
                pass

        with patch("backend.broker.WebAIClient", return_value=DirectClient()):
            answer = self.service.broker.request(job["id"], prepared_question())
            self.service.broker.start()
            result = self.wait_answer(job["id"], answer["id"])
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(json.loads(result["answer"]), {"Answer": ["B"]})
        self.assertEqual(calls, [1])
        self.assertEqual(len(self.calls), 0)  # 网页 AI 没有被调用

        # 关掉直答后回到三步流水线（网页通道不在链上时直接走流水线）
        self.client.put("/v1/ai/vision-direct", headers=self.headers, json={"enabled": False})
        self.assertFalse(self.service.broker.direct_vision())
        self.assertFalse(self.service.broker.channel_status()["vision_direct"])

    def test_failed_image_api_falls_back_to_the_web_channel(self):
        job = self.job()
        # 文本走 API；图片题也走 API，但图片 API 这次失败 → 自动退回网页 AI 完成
        self.client.put("/v1/ai/provider", headers=self.headers, json={
            "endpoint": "https://api.example.com/v1", "model": "api-text", "api_key": "offline-private-key",
        })
        self.client.put("/v1/ai/channel", headers=self.headers, json={"channel": "api", "kind": "vision"})
        self.assertEqual(self.service.broker.channel_chain("vision"), ["api", "web"])

        broken = Mock(
            complete_direct=Mock(side_effect=VisionUnavailableError("offline api vision down")),
            complete=Mock(side_effect=WebAIError("offline api down")),
            complete_vision=Mock(side_effect=VisionUnavailableError("offline api vision down")),
            close=Mock(),
            last_diagnostics=None,
            selected_model="api-vision",
        )
        with patch("backend.broker.WebAIClient", return_value=broken):
            answer = self.service.broker.request(job["id"], prepared_question())
            self.service.broker.start()
            result = self.wait_answer(job["id"], answer["id"])
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(json.loads(result["answer"]), {"Answer": ["B. two"]})
        self.assertEqual(len(self.calls), 1)  # 网页 AI 那一遍真的被调用了

        # 关掉回退后同样的失败就是直接失败
        self.client.put("/v1/ai/vision-fallback", headers=self.headers, json={"enabled": False})
        self.assertEqual(self.service.broker.channel_chain("vision"), ["api"])
        self.client.put("/v1/ai/vision-fallback", headers=self.headers, json={"enabled": True})

    def test_rejected_ai_output_is_not_copied_into_broker_logs_or_the_bank(self):
        job = self.job()
        secret = "private-image-data-that-must-not-be-logged"
        fake = Mock(complete=Mock(return_value=secret))
        self.service.broker.client_factory = lambda: fake
        answer = self.service.broker.request(job["id"], prepared_question())
        with self.assertLogs("backend.broker", level="WARNING") as logs:
            self.service.broker.start()
            result = self.wait_answer(job["id"], answer["id"])
        self.assertEqual(result["state"], "error")
        self.assertIsNone(result["answer"])
        self.assertNotIn(secret, str(logs.output))
        self.assertIn("response_chars=", str(logs.output))
        self.assertEqual(fake.complete.call_count, 3)


class ModelSettingsTests(unittest.TestCase):
    def test_fallbacks_are_validated_propagated_and_visible_without_credentials(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(
            state_dir=Path(directory.name), ai_model="text-primary", ai_vision_model="vision-primary",
            ai_model_fallbacks='["text-backup"]', ai_vision_model_fallbacks=["vision-backup"],
        )
        service = Service(settings, client_factory=Mock)
        self.addCleanup(service.close)
        self.assertEqual(service.broker.config["webai_model_fallbacks"], ("text-backup",))
        self.assertEqual(service.broker.config["webai_vision_model_fallbacks"], ("vision-backup",))
        response = create_app(service).test_client().get("/v1/health", headers={
            "Authorization": "Bearer " + service.secrets.token,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ai_model"], "text-primary")
        self.assertEqual(response.json["ai_model_fallbacks"], ["text-backup"])
        self.assertEqual(response.json["ai_vision_model_fallbacks"], ["vision-backup"])
        self.assertNotIn(service.secrets.token, response.text)
        for value in ('"not-an-array"', {"backup": "model"}, ["a", "b", "c", "d", "e", "f", "g"],
                      ["unsafe\nmodel"]):
            with self.subTest(value=value), self.assertRaises(WebAIError):
                Settings(ai_vision_model_fallbacks=value)

    def test_api_answering_channel_is_separate_persistent_and_requires_configuration(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(
            state_dir=Path(directory.name),
            ai_api_endpoint="https://api.example.com/v1",
            ai_api_model="api-text",
            ai_api_vision_model="api-vision",
            ai_api_key_env="OFFLINE_ANSWER_API_KEY",
        )
        with patch.dict(os.environ, {"OFFLINE_ANSWER_API_KEY": "offline-private-key"}):
            service = Service(settings, client_factory=Mock)
            self.addCleanup(service.close)
            self.assertEqual(service.broker.api_config["webai_endpoint"], "https://api.example.com/v1")
            self.assertEqual(service.broker.api_config["webai_vision_force"], "true")
            api_client = WebAIClient(service.broker.api_config)
            self.assertEqual(api_client.model, "api-text")
            self.assertEqual(api_client.vision_model, "api-vision")
            api_client.close()
            client = create_app(service).test_client()
            headers = {"Authorization": "Bearer " + service.secrets.token}
            health = client.get("/v1/health", headers=headers)
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json["ai_channel"], "web")
            self.assertTrue(health.json["ai_api_configured"])
            self.assertEqual(health.json["ai_api_model"], "api-text")
            self.assertNotIn("offline-private-key", health.text)
            switched = client.put("/v1/ai/channel", headers=headers, json={"channel": "api"})
            self.assertEqual(switched.status_code, 200)
            self.assertEqual(switched.json["channel"], "api")
            self.assertEqual(client.get("/v1/health", headers=headers).json["ai_channel"], "api")
            service.close()
            service = Service(settings, client_factory=Mock)
            self.addCleanup(service.close)
            self.assertEqual(service.broker.channel(), "api")

        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        service = Service(Settings(state_dir=Path(other.name)), client_factory=Mock)
        headers = {"Authorization": "Bearer " + service.secrets.token}
        response = create_app(service).test_client().put(
            "/v1/ai/channel", headers=headers, json={"channel": "api"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(service.broker.channel(), "web")
        service.close()

    def test_text_and_image_channels_are_configured_separately(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(
            state_dir=Path(directory.name),
            ai_api_endpoint="https://api.example.com/v1",
            ai_api_model="api-text",
            ai_api_key_env="OFFLINE_ANSWER_API_KEY",
        )
        with patch.dict(os.environ, {"OFFLINE_ANSWER_API_KEY": "offline-private-key"}):
            service = Service(settings, client_factory=Mock)
            self.addCleanup(service.close)
            client = create_app(service).test_client()
            headers = {"Authorization": "Bearer " + service.secrets.token}

            # 默认跟随文本通道：图片题没有单独设置时不产生"第三状态"
            status = client.get("/v1/ai/channel", headers=headers).json
            self.assertEqual(status["channel"], "web")
            self.assertEqual(status["vision_channel"], "web")
            self.assertFalse(status["vision_channel_explicit"])
            self.assertFalse(status["vision_enabled"])

            # 文本走 API，图片仍跟随（=API）
            client.put("/v1/ai/channel", headers=headers, json={"channel": "api"})
            status = client.get("/v1/ai/channel", headers=headers).json
            self.assertEqual((status["channel"], status["vision_channel"]), ("api", "api"))
            self.assertFalse(status["vision_channel_explicit"])

            # 只把图片题切到网页 AI：文本不受影响
            switched = client.put(
                "/v1/ai/channel", headers=headers,
                json={"channel": "web", "kind": "vision"},
            )
            self.assertEqual(switched.status_code, 200)
            self.assertEqual(switched.json["channel"], "api")
            self.assertEqual(switched.json["vision_channel"], "web")
            self.assertTrue(switched.json["vision_channel_explicit"])
            self.assertEqual(service.broker.channel("text"), "api")
            self.assertEqual(service.broker.channel("vision"), "web")

            health = client.get("/v1/health", headers=headers).json
            self.assertEqual(
                (health["ai_channel"], health["ai_vision_channel"]), ("api", "web"),
            )
            self.assertFalse(health["ai_vision_enabled"])

            # 图片题自动处理开关
            enabled = client.put("/v1/ai/vision", headers=headers, json={"enabled": True})
            self.assertEqual(enabled.status_code, 200)
            self.assertTrue(enabled.json["vision_enabled"])
            self.assertTrue(
                client.get("/v1/health", headers=headers).json["ai_vision_enabled"],
            )
            self.assertEqual(
                client.put("/v1/ai/vision", headers=headers, json={"enabled": "yes"}).status_code,
                400,
            )

            # 跟随文本：回到单通道行为
            client.put("/v1/ai/channel", headers=headers, json={"channel": "auto", "kind": "vision"})
            self.assertEqual(service.broker.channel("vision"), "api")
            self.assertEqual(
                client.put("/v1/ai/channel", headers=headers, json={"channel": "auto"}).status_code,
                400,
            )
            self.assertEqual(
                client.put(
                    "/v1/ai/channel", headers=headers,
                    json={"channel": "web", "kind": "audio"},
                ).status_code,
                400,
            )

            # 通道设置持久化到数据库，重启后仍在
            client.put("/v1/ai/channel", headers=headers, json={"channel": "web", "kind": "vision"})
            service.close()
            service = Service(settings, client_factory=Mock)
            self.addCleanup(service.close)
            self.assertEqual(service.broker.channel("text"), "api")
            self.assertEqual(service.broker.channel("vision"), "web")
            self.assertTrue(service.broker.channel_status()["vision_enabled"])

    def test_image_api_is_configured_separately_and_inherits_the_text_key(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(state_dir=Path(directory.name))
        service = Service(settings, client_factory=Mock)
        self.addCleanup(service.close)
        client = create_app(service).test_client()
        headers = {"Authorization": "Bearer " + service.secrets.token}

        # 先配置文本 API：图片通道还没单独配置时跟随它
        saved = client.put("/v1/ai/provider", headers=headers, json={
            "endpoint": "https://api.example.com/v1", "model": "api-text", "protocol": "chat",
            "api_key": "offline-private-key",
        })
        self.assertEqual(saved.status_code, 200)
        inherited = client.get("/v1/ai/vision-provider", headers=headers).json
        self.assertFalse(inherited["configured"])
        self.assertTrue(inherited["inherited"])
        self.assertEqual(inherited["model"], "api-text")
        self.assertEqual(service.broker.route_api_config("vision")["webai_model"], "api-text")

        # 只填一个图片模型：端点、key、协议全部沿用文本那套
        separate = client.put("/v1/ai/vision-provider", headers=headers, json={
            "model": "api-vision",
        })
        self.assertEqual(separate.status_code, 200, separate.json)
        self.assertTrue(separate.json["configured"])
        self.assertEqual(separate.json["model"], "api-vision")
        self.assertEqual(separate.json["endpoint"], "https://api.example.com/v1")
        self.assertTrue(separate.json["has_key"])

        text_config = service.broker.route_api_config("text")
        vision_config = service.broker.route_api_config("vision")
        self.assertEqual(text_config["webai_model"], "api-text")
        self.assertEqual(vision_config["webai_model"], "api-vision")
        self.assertEqual(vision_config["webai_vision_model"], "api-vision")
        self.assertEqual(vision_config["webai_endpoint"], text_config["webai_endpoint"])
        self.assertEqual(vision_config["webai_key"], text_config["webai_key"])

        # 图片题走 API 时：主通道 + 失败自动退回网页 AI
        client.put("/v1/ai/channel", headers=headers, json={"channel": "api", "kind": "vision"})
        status = client.get("/v1/ai/channel", headers=headers).json
        self.assertEqual(status["vision_channel"], "api")
        self.assertEqual(status["vision_chain"], ["api", "web"])
        self.assertTrue(status["vision_api"]["separate"])
        self.assertEqual(status["vision_api"]["model"], "api-vision")

        off = client.put("/v1/ai/vision-fallback", headers=headers, json={"enabled": False})
        self.assertEqual(off.status_code, 200)
        self.assertEqual(off.json["vision_chain"], ["api"])
        self.assertEqual(
            client.put(
                "/v1/ai/vision-fallback", headers=headers, json={"enabled": "yes"},
            ).status_code,
            400,
        )
        client.put("/v1/ai/vision-fallback", headers=headers, json={"enabled": True})

        health = client.get("/v1/health", headers=headers).json
        self.assertTrue(health["ai_vision_api_configured"])
        self.assertTrue(health["ai_vision_api_separate"])
        self.assertEqual(health["ai_vision_api_model"], "api-vision")
        self.assertTrue(health["ai_vision_fallback"])

        # 正确率测试：没有可对照的图片题时如实返回 0，参数错误则拒绝
        bench = client.post(
            "/v1/ai/vision-bench", headers=headers, json={"limit": 6, "mode": "direct"},
        )
        self.assertEqual(bench.status_code, 200, bench.json)
        self.assertEqual(bench.json["available"], 0)
        self.assertIsNone(bench.json["accuracy"])
        for bad in ({"mode": "magic"}, {"limit": 0}, {"limit": 99}, {"other": 1}):
            self.assertEqual(
                client.post("/v1/ai/vision-bench", headers=headers, json=bad).status_code, 400,
            )

        # 清掉图片通道后回到跟随文本
        service.store.save_provider_settings(
            {}, clear=("model", "vision_model"), prefix="ai_vision_api_",
        )
        self.assertFalse(service.vision_provider.client_config())

    def test_vision_bench_grades_against_reference_answers(self):
        from backend.vision_bench import grade, label

        single = {"type": "single", "options": "A. 甲\nB. 乙"}
        self.assertEqual(label("B. [image_3]"), "B")
        self.assertTrue(grade(single, ["B. [image_3]"], ["B"]))
        self.assertFalse(grade(single, ["B. [image_3]"], ["C"]))
        self.assertIsNone(grade(single, ["B. [image_3]"], []))
        multiple = {"type": "multiple", "options": "A. 甲\nB. 乙\nC. 丙"}
        self.assertTrue(grade(multiple, ["A", "C"], ["C", "A"]))
        self.assertFalse(grade(multiple, ["A", "C"], ["A"]))
        judge = {"type": "judge"}
        self.assertTrue(grade(judge, ["true"], ["true"]))
        self.assertFalse(grade(judge, ["true"], ["false"]))

    def test_image_question_switch_reaches_the_worker_payload(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = Service(Settings(state_dir=Path(directory.name)), client_factory=Mock)
        self.addCleanup(service.close)
        account = service.store.save_account("Someone", "someone", "secret")
        job, _ = service.store.create_job(
            account["id"],
            {"course_ids": ["1"], "submit": False, "preview": True, "experimental_vision": False},
        )
        payload = service.store.payload(job["id"])
        self.assertFalse(payload["spec"]["experimental_vision"])
        service.store.set_setting("ai_vision_enabled", "1")
        self.assertTrue(service.store.payload(job["id"])["spec"]["experimental_vision"])
        # 任务自身勾选过识图时不会被全局开关覆盖掉
        service.store.set_setting("ai_vision_enabled", "0")
        other = service.store.save_account("Other", "other", "secret")
        explicit, _ = service.store.create_job(
            other["id"],
            {"course_ids": ["1"], "submit": False, "preview": True, "experimental_vision": True},
        )
        self.assertTrue(service.store.payload(explicit["id"])["spec"]["experimental_vision"])


if __name__ == "__main__":
    unittest.main()
