"""Short authenticated polling requests, never a long-lived API worker connection."""

import time
from urllib.parse import urlsplit

import requests

from api.runtime import report
from api.webai_client import VisionUnavailableError, WebAIError, validate_question
from api.webai_errors import RateLimitedError, VisionReviewError


class BrokerClient:
    def __init__(self, config):
        self.endpoint = config["webai_broker_endpoint"].rstrip("/")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise WebAIError("The worker broker must use the private loopback endpoint")
        self.token = config["webai_broker_token"]
        self.timeout = float(config.get("webai_broker_timeout", 3600))
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + self.token

    @staticmethod
    def _report(question, state, error=""):
        report(
            "ai", ai_state=state,
            ai_question=str(question.get("title", ""))[:300],
            ai_type=str(question.get("type", "")),
            ai_error=error,
            ai_mode="vision" if question.get("images") else "text",
            ai_image_count=len(question.get("images", [])),
        )

    def _request(self, method, path, **kwargs):
        try:
            with self.session.request(
                method, self.endpoint + path, timeout=(3, 10), allow_redirects=False, **kwargs
            ) as response:
                if response.status_code not in {200, 202}:
                    raise WebAIError(f"AI broker returned HTTP {response.status_code}")
                data = response.json()
                if not isinstance(data, dict):
                    raise WebAIError("Invalid AI broker response")
                return data
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, WebAIError):
                raise
            raise WebAIError("AI broker request failed") from exc

    def complete(self, question):
        validate_question(question)
        try:
            deadline = time.monotonic() + self.timeout
            data = self._request("POST", "/answers", json=question)
            self.last_source = data.get("source", "ai")
            answer_id = data.get("id")
            if not isinstance(answer_id, str) or not answer_id:
                raise WebAIError("AI broker returned an invalid answer id")
            if data.get("state") == "done" and isinstance(data.get("answer"), str):
                self.last_source = "bank"
                self._report(question, "cached")
                return data["answer"]
            while data.get("state") in {"queued", "running"}:
                state = "shared_wait" if self.last_source == "shared" else data["state"]
                if question.get("images") and state in {"queued", "running"}:
                    state = "vision_" + state
                stage = data.get("stage")
                if question.get("images") and stage in {"recognizing", "solving", "verifying"}:
                    state = "vision_" + stage
                self._report(question, state)
                if time.monotonic() >= deadline:
                    raise WebAIError("AI queue wait deadline exceeded; manual retry is required")
                time.sleep(2)
                data = self._request("GET", "/answers/" + answer_id)
            if data.get("state") != "done" or not isinstance(data.get("answer"), str):
                reason = str(data.get("error") or data.get("state") or "unknown")[:180]
                if reason == "AI_VISION_UNAVAILABLE":
                    raise VisionUnavailableError("图片 AI 未就绪：请登录支持识图的网页模型，或配置独立视觉 API")
                if reason == "AI_RATE_LIMITED":
                    raise RateLimitedError("AI 上游限流，请等待恢复后重试；本次未生成或提交答案")
                if reason in {"AI_VISION_UNREADABLE", "AI_VISION_READING_MISMATCH", "AI_VISION_DISAGREEMENT"}:
                    raise VisionReviewError(reason)
                raise WebAIError(f"AI broker could not obtain a validated answer ({reason})")
            self._report(question, "shared" if self.last_source == "shared" else "answered")
            return data["answer"]
        except VisionUnavailableError:
            self._report(question, "vision_unavailable", "图片 AI 未配置或未登录")
            raise
        except VisionReviewError as exc:
            message = {
                "AI_VISION_UNREADABLE": "图片细节不清楚，未猜答",
                "AI_VISION_READING_MISMATCH": "原图与识别内容不一致，需人工核对",
                "AI_VISION_DISAGREEMENT": "两路答案不一致，未提交或写入共享题库",
            }[exc.code]
            self._report(question, "vision_review", message)
            raise WebAIError(message) from exc
        except RateLimitedError as exc:
            self._report(question, "error", str(exc))
            raise
        except WebAIError:
            self._report(question, "error", "AI 查询失败")
            raise

    def close(self):
        self.session.close()
