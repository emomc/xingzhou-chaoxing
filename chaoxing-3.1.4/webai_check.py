"""Check a WebAI bridge without signing into or changing a course."""

import argparse
import base64
import configparser
import json
from pathlib import Path
import re
import sys

from api.webai_client import WebAIClient, WebAIError, parse_answers
from api.question_images import MAX_IMAGE_BYTES, inspect_image, prepare_question_images


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="config.ini")
    parser.add_argument("--models", action="store_true", help="List bridge models without asking a question")
    parser.add_argument("--vision-models", action="store_true", help="List vision capabilities without changing a course")
    parser.add_argument("--question", help="Ask one practice question; use [image_1] markers for image positions")
    parser.add_argument("--image", action="append", default=[], help="Local image file; repeat in image marker order (max 10)")
    parser.add_argument("--type", choices=["single", "multiple", "judgement", "completion", "shortanswer"],
                        default="single")
    parser.add_argument("--option", action="append", default=[], help="Repeat for each option, e.g. 'A. two'")
    args = parser.parse_args(argv)
    if args.image and (not args.question or len(args.image) > 10):
        parser.error("--image requires --question and accepts at most 10 image files")
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(args.config, encoding="utf-8") or "tiku" not in config:
        print("Cannot read the [tiku] section of the specified config", file=sys.stderr)
        return 2
    client = None
    try:
        client = WebAIClient(config["tiku"])
        if args.vision_models:
            models = client.model_details(vision=True)
            print(json.dumps({
                "vision_models": models, "configured_vision_model": client.vision_model,
            }, ensure_ascii=False, indent=2))
            client.select_vision_model()
        if args.models or (not args.image and not args.vision_models):
            models = client.models()
            if args.models or not args.question:
                print(json.dumps({"models": models, "configured_model": client.model,
                                  "model_available": client.model in models}, ensure_ascii=False, indent=2))
            if client.model not in models:
                raise WebAIError("Configured text model is unavailable; select a listed model in config.ini")
        if args.question:
            question = {"title": args.question, "type": args.type, "options": args.option}
            if args.image:
                question["images"] = []
                content = question["title"] + "\n" + "\n".join(args.option)
                for index, filename in enumerate(args.image, 1):
                    try:
                        with Path(filename).open("rb") as stream:
                            raw = stream.read(MAX_IMAGE_BYTES + 1)
                    except OSError as exc:
                        raise WebAIError("Cannot read a specified local image file") from exc
                    raw, mime = inspect_image(raw)
                    image_id = f"image_{index}"
                    if not re.search(rf"\[{image_id}\]", content):
                        question["title"] += f"\n[{image_id}]"
                    question["images"].append({
                        "id": image_id,
                        "source": f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"),
                    })
                question["has_images"] = True
                question = prepare_question_images(question)
            answers = parse_answers(client.complete(question), question)
            print(json.dumps({"model": client.selected_vision_model if args.image else client.model,
                              "image_count": len(args.image), "Answer": answers, "course_changed": False},
                             ensure_ascii=False, indent=2))
        return 0
    except WebAIError as exc:
        print(f"WebAI check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
